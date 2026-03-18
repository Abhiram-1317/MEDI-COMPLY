"""
MEDI-COMPLY — Asynchronous inter-agent message bus.

Provides publish / subscribe semantics backed by ``asyncio.Queue``, plus a
synchronous request-response helper with configurable timeout.  Failed
messages are routed to a dead-letter queue for later inspection.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from medi_comply.core.logger import get_logger, log_action
from medi_comply.core.message_models import AgentMessage, AgentResponse
from medi_comply.schemas.common import ResponseStatus

# Type alias for subscriber callbacks
SubscriberCallback = Callable[[AgentMessage], Awaitable[AgentResponse]]


class MessageDeliveryError(Exception):
    """Raised when a message cannot be delivered to its target agent."""

    def __init__(self, message: AgentMessage, reason: str) -> None:
        self.message = message
        self.reason = reason
        super().__init__(f"Delivery failed for message {message.message_id}: {reason}")


class AsyncMessageBus:
    """Asynchronous message bus for inter-agent communication.

    Features
    --------
    * Topic-based routing (``to_agent`` field on messages).
    * Async publish / subscribe with callback handlers.
    * Synchronous-style ``request_response`` with configurable timeout.
    * Dead-letter queue for undeliverable / failed messages.
    * Full message history retained for audit.
    * ``trace_id`` propagation on all messages in a workflow.
    """

    def __init__(self, max_queue_size: int = 1000) -> None:
        self._queues: dict[str, asyncio.Queue[AgentMessage]] = {}
        self._subscribers: dict[str, SubscriberCallback] = {}
        self._dead_letters: list[dict[str, Any]] = []
        self._message_history: list[AgentMessage] = []
        self._response_history: list[AgentResponse] = []
        self._max_queue_size: int = max_queue_size
        self._running: bool = False
        self._dispatch_tasks: dict[str, asyncio.Task] = {}  # type: ignore[type-arg]
        self._logger = get_logger(agent_name="MessageBus")

    # -- Properties --------------------------------------------------------

    @property
    def dead_letters(self) -> list[dict[str, Any]]:
        """Return a copy of the dead-letter queue."""
        return list(self._dead_letters)

    @property
    def message_history(self) -> list[AgentMessage]:
        """Return a copy of the full message history."""
        return list(self._message_history)

    @property
    def response_history(self) -> list[AgentResponse]:
        """Return a copy of the full response history."""
        return list(self._response_history)

    # -- Public API --------------------------------------------------------

    async def publish(self, message: AgentMessage) -> None:
        """Publish a message to the bus.

        The message is placed into the target agent's queue.  If no queue
        exists yet one is created automatically.

        Parameters
        ----------
        message:
            The :class:`AgentMessage` to publish.
        """
        self._message_history.append(message)
        target = message.to_agent

        if target not in self._queues:
            self._queues[target] = asyncio.Queue(maxsize=self._max_queue_size)

        try:
            self._queues[target].put_nowait(message)
            log_action(
                self._logger,
                action="message_published",
                message=f"Message {message.message_id} -> {target}",
                trace_id=message.trace_id,
            )
        except asyncio.QueueFull:
            self._dead_letters.append(
                {
                    "message": message.model_dump(),
                    "reason": "queue_full",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            log_action(
                self._logger,
                action="dead_letter",
                message=f"Queue full for {target}, message {message.message_id} dead-lettered",
                trace_id=message.trace_id,
            )

    def subscribe(self, agent_name: str, callback: SubscriberCallback) -> None:
        """Register a callback for messages addressed to *agent_name*.

        Parameters
        ----------
        agent_name:
            The agent identifier to listen for.
        callback:
            An async callable ``(AgentMessage) -> AgentResponse``.
        """
        self._subscribers[agent_name] = callback
        if agent_name not in self._queues:
            self._queues[agent_name] = asyncio.Queue(maxsize=self._max_queue_size)
        log_action(
            self._logger,
            action="subscriber_registered",
            message=f"Agent '{agent_name}' subscribed to message bus",
        )

    async def request_response(
        self,
        message: AgentMessage,
        timeout_ms: Optional[int] = None,
    ) -> AgentResponse:
        """Send a message and wait for a synchronous response.

        Parameters
        ----------
        message:
            The outbound message.  ``requires_response`` is forced to ``True``.
        timeout_ms:
            Timeout in milliseconds.  Falls back to ``message.timeout_ms``.

        Returns
        -------
        AgentResponse
            The agent's reply.

        Raises
        ------
        asyncio.TimeoutError
            If no response is received within the timeout window.
        MessageDeliveryError
            If the target agent has no registered subscriber.
        """
        message.requires_response = True
        timeout_s = (timeout_ms or message.timeout_ms) / 1000.0
        target = message.to_agent

        callback = self._subscribers.get(target)
        if callback is None:
            self._dead_letters.append(
                {
                    "message": message.model_dump(),
                    "reason": "no_subscriber",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            raise MessageDeliveryError(message, f"No subscriber for agent '{target}'")

        self._message_history.append(message)

        try:
            response: AgentResponse = await asyncio.wait_for(
                callback(message), timeout=timeout_s
            )
            self._response_history.append(response)
            log_action(
                self._logger,
                action="request_response_completed",
                message=(
                    f"Response {response.response_id} from {target} "
                    f"(confidence={response.confidence_score:.2f})"
                ),
                trace_id=message.trace_id,
            )
            return response
        except asyncio.TimeoutError:
            self._dead_letters.append(
                {
                    "message": message.model_dump(),
                    "reason": "timeout",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            log_action(
                self._logger,
                action="request_timeout",
                message=f"Timeout waiting for {target} (message {message.message_id})",
                trace_id=message.trace_id,
            )
            raise

    # -- Dispatcher loop ---------------------------------------------------

    async def start(self) -> None:
        """Start background dispatch loops for all registered subscribers."""
        self._running = True
        for agent_name in self._subscribers:
            task = asyncio.create_task(self._dispatch_loop(agent_name))
            self._dispatch_tasks[agent_name] = task
        log_action(self._logger, action="bus_started", message="Message bus started")

    async def stop(self) -> None:
        """Gracefully stop all dispatch loops."""
        self._running = False
        for task in self._dispatch_tasks.values():
            task.cancel()
        self._dispatch_tasks.clear()
        log_action(self._logger, action="bus_stopped", message="Message bus stopped")

    async def _dispatch_loop(self, agent_name: str) -> None:
        """Internal loop: dequeue messages and invoke the subscriber callback."""
        queue = self._queues[agent_name]
        callback = self._subscribers[agent_name]
        while self._running:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                response = await callback(message)
                self._response_history.append(response)
            except Exception as exc:
                self._dead_letters.append(
                    {
                        "message": message.model_dump(),
                        "reason": f"handler_error: {exc!s}",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )

    # -- Dunder ------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"AsyncMessageBus(subscribers={list(self._subscribers.keys())}, "
            f"history={len(self._message_history)}, "
            f"dead_letters={len(self._dead_letters)})"
        )
