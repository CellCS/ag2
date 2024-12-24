# Copyright (c) 2023 - 2024, Owners of https://github.com/ag2ai
#
# SPDX-License-Identifier: Apache-2.0
#
# Portions derived from  https://github.com/microsoft/autogen are under the MIT License.
# SPDX-License-Identifier: MIT

# import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Literal, Optional

from asyncer import TaskGroup, asyncify, create_task_group
from openai import AsyncOpenAI
from openai.resources.beta.realtime.realtime import AsyncRealtimeConnection
from openai.types.beta.realtime.realtime_server_event import RealtimeServerEvent

from ..contrib.swarm_agent import AfterWorkOption, initiate_swarm_chat

if TYPE_CHECKING:
    from .function_observer import FunctionObserver
    from .realtime_agent import RealtimeAgent
    from .realtime_observer import RealtimeObserver

logger = logging.getLogger(__name__)

# define role literal type for typing
Role = Literal["user", "assistant", "system"]


class OpenAIRealtimeClient:
    """(Experimental) Client for OpenAI Realtime API."""

    def __init__(
        self, agent: "RealtimeAgent", audio_adapter: "RealtimeObserver", function_observer: "FunctionObserver"
    ) -> None:
        """(Experimental) Client for OpenAI Realtime API.

        Args:
            agent (RealtimeAgent): The agent that the client is associated with.
            audio_adapter (RealtimeObserver): The audio adapter for the client.
            function_observer (FunctionObserver): The function observer for the client.

        """
        self._agent = agent
        self._observers: list["RealtimeObserver"] = []
        self._connection: Optional[AsyncRealtimeConnection] = None
        self.client = AsyncOpenAI()
        self.register(audio_adapter)
        self.register(function_observer)

        # LLM config
        llm_config = self._agent.llm_config

        config: dict[str, Any] = llm_config["config_list"][0]  # type: ignore[index]

        self.model: str = config["model"]
        self.temperature: float = llm_config["temperature"]  # type: ignore[index]
        self.api_key: str = config["api_key"]

        # create a task group to manage the tasks
        self.tg: Optional[TaskGroup] = None

    @property
    def connection(self) -> AsyncRealtimeConnection:
        """Get the OpenAI WebSocket connection."""
        if self._connection is None:
            raise RuntimeError("OpenAI WebSocket is not initialized")
        return self._connection

    def register(self, observer: "RealtimeObserver") -> None:
        """Register an observer to the client."""
        observer.register_client(self)
        self._observers.append(observer)

    async def notify_observers(self, event: RealtimeServerEvent) -> None:
        """Notify all observers of a event from the OpenAI Realtime API.

        Args:
            event (RealtimeServerEvent): The message from the OpenAI Realtime API.

        """
        for observer in self._observers:
            await observer.update(event)

    async def function_result(self, call_id: str, result: str) -> None:
        """Send the result of a function call to the OpenAI Realtime API.

        Args:
            call_id (str): The ID of the function call.
            result (str): The result of the function call.
        """
        await self.connection.conversation.item.create(
            item={
                "type": "function_call_output",
                "call_id": call_id,
                "output": result,
            },
        )

        await self.connection.response.create()

    async def send_text(self, *, role: Role, text: str) -> None:
        """Send a text message to the OpenAI Realtime API.

        Args:
            role (str): The role of the message.
            text (str): The text of the message.
        """

        if self.connection is None:
            raise RuntimeError("OpenAI WebSocket is not initialized")

        await self.connection.response.cancel()
        await self.connection.conversation.item.create(
            item={"type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}
        )
        await self.connection.response.create()

    async def send_audio(self, audio: str) -> None:
        """Send audio to the OpenAI Realtime API.

        Args:
            audio (str): The audio to send.
        """
        await self.connection.input_audio_buffer.append(audio=audio)

    async def truncate_audio(self, audio_end_ms: int, content_index: int, item_id: str) -> None:
        """Truncate audio in the OpenAI Realtime API.

        Args:
            audio_end_ms (int): The end of the audio to truncate.
            content_index (int): The index of the content to truncate.
            item_id (str): The ID of the item to truncate.
        """
        await self.connection.conversation.item.truncate(
            audio_end_ms=audio_end_ms, content_index=content_index, item_id=item_id
        )

    # todo override in specific clients
    async def initialize_session(self) -> None:
        """Control initial session with OpenAI."""
        session_update = {
            # todo: move to config
            "turn_detection": {"type": "server_vad"},
            "voice": self._agent.voice,
            "instructions": self._agent.system_message,
            "modalities": ["audio", "text"],
            "temperature": 0.8,
        }
        await self.session_update(session_options=session_update)

    # todo override in specific clients
    async def session_update(self, session_options: dict[str, Any]) -> None:
        """Send a session update to the OpenAI Realtime API.

        Args:
            session_options (dict[str, Any]): The session options to update.
        """
        logger.info(f"Sending session update: {session_options}")
        await self.connection.session.update(session=session_options)  # type: ignore[arg-type]
        logger.info("Sending session update finished")

    async def _read_from_client(self) -> None:
        """Read messages from the OpenAI Realtime API."""
        try:
            async for event in self.connection:
                await self.notify_observers(event)
        except Exception as e:
            logger.warning(f"Error in _read_from_client: {e}")

    async def run(self) -> None:
        """Run the client."""
        async with self.client.beta.realtime.connect(
            model=self.model,
            extra_headers={
                "Authorization": f"Bearer {self.api_key}",
                "OpenAI-Beta": "realtime=v1",
            },
        ) as connection:
            self._connection = connection

            await self.initialize_session()
            async with create_task_group() as tg:
                self.tg = tg
                self.tg.soonify(self._read_from_client)()
                for observer in self._observers:
                    self.tg.soonify(observer.run)()

                initial_agent = self._agent._initial_agent
                agents = self._agent._agents
                user_agent = self._agent

                if (initial_agent and agents) and self._agent._start_swarm_chat:
                    self.tg.soonify(asyncify(initiate_swarm_chat))(
                        initial_agent=initial_agent,
                        agents=agents,
                        user_agent=user_agent,  # type: ignore[arg-type]
                        messages="Find out what the user wants.",
                        after_work=AfterWorkOption.REVERT_TO_USER,
                    )
