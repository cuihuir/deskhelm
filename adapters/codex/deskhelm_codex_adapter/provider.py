from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import select
import signal
import subprocess
from threading import Event
import time
from typing import Any

from deskhelm_bridge.agent_gateway import (
    AgentProviderEvent,
    AgentRunRequest,
    AgentRunResult,
    AgentRunStatus,
)
from deskhelm_bridge.interaction import (
    InteractionKind,
    MessagePayload,
    MessagePhase,
    MessageRole,
    ToolPayload,
    ToolPhase,
)


MAX_CODEX_JSONL_BYTES = 1024 * 1024
PROCESS_POLL_SECONDS = 0.1
TERMINATE_GRACE_SECONDS = 1.0


@dataclass(slots=True)
class CodexJsonMapper:
    provider_session_id: str = ""
    terminal_result: AgentRunResult | None = None

    def consume(self, line: bytes) -> tuple[AgentProviderEvent, ...]:
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Codex emitted malformed JSONL") from error
        if not isinstance(value, dict):
            raise ValueError("Codex JSONL event must be an object")
        event_type = value.get("type")
        if not isinstance(event_type, str):
            raise ValueError("Codex JSONL event type must be a string")

        if event_type == "thread.started":
            thread_id = value.get("thread_id")
            if isinstance(thread_id, str) and thread_id.strip():
                self.provider_session_id = thread_id
            return ()
        if event_type == "turn.completed":
            self.terminal_result = AgentRunResult(
                status=AgentRunStatus.COMPLETED,
                provider_session_id=self.provider_session_id,
            )
            return ()
        if event_type in {"turn.failed", "error"}:
            self.terminal_result = AgentRunResult(
                status=AgentRunStatus.FAILED,
                message=self._error_message(value),
                error_code="codex_error",
                provider_session_id=self.provider_session_id,
            )
            return ()
        if event_type not in {"item.started", "item.completed"}:
            return ()

        item = value.get("item")
        if not isinstance(item, dict):
            raise ValueError("Codex item event must include an item object")
        item_id = item.get("id")
        item_type = item.get("type")
        if not isinstance(item_id, str) or not item_id.strip():
            raise ValueError("Codex item id must not be empty")
        if item_type == "agent_message" and event_type == "item.completed":
            text = item.get("text")
            if not isinstance(text, str):
                raise ValueError("Codex agent message text must be a string")
            return (
                AgentProviderEvent(
                    kind=InteractionKind.MESSAGE,
                    correlation_id=item_id,
                    payload=MessagePayload(
                        role=MessageRole.ASSISTANT,
                        phase=MessagePhase.COMPLETE,
                        text=text,
                    ),
                ),
            )
        if item_type == "command_execution":
            phase = (
                ToolPhase.START
                if event_type == "item.started"
                else ToolPhase.COMPLETE
            )
            exit_code = item.get("exit_code")
            if not isinstance(exit_code, int) or isinstance(exit_code, bool):
                exit_code = None
            return (
                AgentProviderEvent(
                    kind=InteractionKind.TOOL,
                    correlation_id=item_id,
                    payload=ToolPayload(
                        tool_call_id=item_id,
                        name="command_execution",
                        phase=phase,
                        exit_code=exit_code if phase is ToolPhase.COMPLETE else None,
                    ),
                ),
            )
        return ()

    @staticmethod
    def _error_message(value: dict[str, Any]) -> str:
        error = value.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message[:512]
        message = value.get("message")
        if isinstance(message, str) and message.strip():
            return message[:512]
        return "Codex run failed"


@dataclass(slots=True)
class CodexExecProvider:
    command_prefix: tuple[str, ...] = ("codex",)
    sandbox: str = "read-only"
    timeout_seconds: float = 300.0
    source: str = "codex-exec-json"
    source_version: str = field(default="unknown", init=False)

    def __post_init__(self) -> None:
        if not self.command_prefix or not all(self.command_prefix):
            raise ValueError("command_prefix must not be empty")
        if self.sandbox not in {"read-only", "workspace-write"}:
            raise ValueError("Codex sandbox must be read-only or workspace-write")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self.source_version = self._detect_version()

    def run(
        self,
        request: AgentRunRequest,
        emit: Callable[[AgentProviderEvent], None],
        cancel: Event,
    ) -> AgentRunResult:
        mapper = CodexJsonMapper(provider_session_id=request.provider_session_id)
        command = self._command(request)
        try:
            process = subprocess.Popen(
                command,
                cwd=request.working_directory,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            return AgentRunResult(
                status=AgentRunStatus.FAILED,
                message="Codex executable could not be started",
                error_code="process_start_failed",
                provider_session_id=request.provider_session_id,
            )
        assert process.stdin is not None
        assert process.stdout is not None
        deadline = time.monotonic() + self.timeout_seconds
        input_status = self._write_prompt(
            process,
            request.prompt.encode("utf-8"),
            cancel,
            deadline,
        )
        if input_status is not None:
            self._terminate(process)
            if input_status is AgentRunStatus.CANCELLED:
                return AgentRunResult(
                    status=AgentRunStatus.CANCELLED,
                    provider_session_id=request.provider_session_id,
                )
            if input_status is AgentRunStatus.TIMED_OUT:
                return AgentRunResult(
                    status=AgentRunStatus.TIMED_OUT,
                    error_code="codex_timeout",
                    provider_session_id=request.provider_session_id,
                )
            return AgentRunResult(
                status=AgentRunStatus.FAILED,
                message="Codex prompt input could not be delivered",
                error_code="process_input_failed",
                provider_session_id=request.provider_session_id,
            )
        try:
            output_descriptor = process.stdout.fileno()
            os.set_blocking(output_descriptor, False)
            output_buffer = bytearray()
            while True:
                if cancel.is_set():
                    self._terminate(process)
                    return AgentRunResult(
                        status=AgentRunStatus.CANCELLED,
                        provider_session_id=mapper.provider_session_id,
                    )
                if time.monotonic() >= deadline:
                    self._terminate(process)
                    return AgentRunResult(
                        status=AgentRunStatus.TIMED_OUT,
                        error_code="codex_timeout",
                        provider_session_id=mapper.provider_session_id,
                    )

                readable, _, _ = select.select(
                    [process.stdout], [], [], PROCESS_POLL_SECONDS
                )
                if readable:
                    try:
                        chunk = os.read(output_descriptor, 65536)
                    except BlockingIOError:
                        chunk = None
                    if chunk:
                        output_buffer.extend(chunk)
                        while True:
                            newline = output_buffer.find(b"\n")
                            if newline < 0:
                                break
                            if newline > MAX_CODEX_JSONL_BYTES:
                                self._terminate(process)
                                return self._invalid_jsonl_result(
                                    mapper, "Codex emitted an oversized JSONL frame"
                                )
                            line = bytes(output_buffer[: newline + 1])
                            del output_buffer[: newline + 1]
                            try:
                                events = mapper.consume(line)
                            except ValueError:
                                self._terminate(process)
                                return self._invalid_jsonl_result(
                                    mapper, "Codex emitted malformed JSONL"
                                )
                            for event in events:
                                emit(event)
                        if len(output_buffer) > MAX_CODEX_JSONL_BYTES:
                            self._terminate(process)
                            return self._invalid_jsonl_result(
                                mapper, "Codex emitted an oversized JSONL frame"
                            )
                        continue
                    if chunk is None:
                        continue

                    if output_buffer:
                        self._terminate(process)
                        return self._invalid_jsonl_result(
                            mapper, "Codex emitted an incomplete JSONL frame"
                        )
                    try:
                        return_code = process.wait(timeout=PROCESS_POLL_SECONDS)
                    except subprocess.TimeoutExpired:
                        continue
                    if mapper.terminal_result is not None:
                        if return_code != 0 and (
                            mapper.terminal_result.status
                            is AgentRunStatus.COMPLETED
                        ):
                            return AgentRunResult(
                                status=AgentRunStatus.FAILED,
                                message="Codex process exited unsuccessfully",
                                error_code="process_exit",
                                provider_session_id=mapper.provider_session_id,
                            )
                        return mapper.terminal_result
                    return AgentRunResult(
                        status=AgentRunStatus.FAILED,
                        message="Codex stream ended without a terminal event",
                        error_code=(
                            "missing_terminal_event"
                            if return_code == 0
                            else "process_exit"
                        ),
                        provider_session_id=mapper.provider_session_id,
                    )
        finally:
            process.stdout.close()
            if process.poll() is None:
                self._terminate(process)

    def _command(self, request: AgentRunRequest) -> list[str]:
        if request.provider_session_id:
            return [
                *self.command_prefix,
                "exec",
                "--sandbox",
                self.sandbox,
                "resume",
                "--json",
                request.provider_session_id,
                "-",
            ]
        return [
            *self.command_prefix,
            "exec",
            "--json",
            "--color",
            "never",
            "--sandbox",
            self.sandbox,
            "--cd",
            str(request.working_directory),
            "-",
        ]

    def _detect_version(self) -> str:
        try:
            result = subprocess.run(
                [*self.command_prefix, "--version"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "unavailable"
        version = result.stdout.strip()
        return version[:128] if version else "unknown"

    @staticmethod
    def _invalid_jsonl_result(
        mapper: CodexJsonMapper, message: str
    ) -> AgentRunResult:
        return AgentRunResult(
            status=AgentRunStatus.FAILED,
            message=message,
            error_code="invalid_jsonl",
            provider_session_id=mapper.provider_session_id,
        )

    @staticmethod
    def _write_prompt(
        process: subprocess.Popen[bytes],
        prompt: bytes,
        cancel: Event,
        deadline: float,
    ) -> AgentRunStatus | None:
        assert process.stdin is not None
        descriptor = process.stdin.fileno()
        offset = 0
        try:
            os.set_blocking(descriptor, False)
            while offset < len(prompt):
                if cancel.is_set():
                    return AgentRunStatus.CANCELLED
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return AgentRunStatus.TIMED_OUT
                _, writable, _ = select.select(
                    [], [process.stdin], [], min(PROCESS_POLL_SECONDS, remaining)
                )
                if not writable:
                    if process.poll() is not None:
                        return AgentRunStatus.FAILED
                    continue
                written = os.write(descriptor, prompt[offset:])
                if written <= 0:
                    return AgentRunStatus.FAILED
                offset += written
            return None
        except OSError:
            return AgentRunStatus.FAILED
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=TERMINATE_GRACE_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                process.wait(timeout=TERMINATE_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass
