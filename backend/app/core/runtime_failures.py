"""Classify runtime errors without exposing provider details to users."""

from __future__ import annotations

import enum
from dataclasses import dataclass

import httpx
from fastapi import HTTPException
from pydantic import ValidationError


class FailureDisposition(enum.StrEnum):
    RETRYABLE = "retryable"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class RuntimeFailure:
    disposition: FailureDisposition
    error_code: str
    message: str
    recovery_action: str


def exception_chain(exc: BaseException) -> tuple[BaseException, ...]:
    """Return the explicit cause/context chain, stopping safely on cycles."""

    current: BaseException | None = exc
    visited: set[int] = set()
    chain: list[BaseException] = []
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return tuple(chain)


def classify_runtime_failure(exc: BaseException) -> FailureDisposition:
    """Classify only operationally retryable failures as retryable."""

    chain = exception_chain(exc)
    status_codes = [
        status_code
        for item in chain
        if (status_code := _http_status_code(item)) is not None
    ]
    if any(
        400 <= status_code < 500 and status_code not in {408, 429}
        for status_code in status_codes
    ):
        return FailureDisposition.TERMINAL
    if any(status_code in {408, 429} or status_code >= 500 for status_code in status_codes):
        return FailureDisposition.RETRYABLE
    if any(isinstance(item, _RETRYABLE_EXCEPTIONS) for item in chain):
        return FailureDisposition.RETRYABLE
    return FailureDisposition.TERMINAL


def describe_runtime_failure(exc: BaseException) -> RuntimeFailure:
    """Return stable, user-safe terminal or retry metadata for a runtime error."""

    disposition = classify_runtime_failure(exc)
    status_code = next(
        (
            code
            for item in exception_chain(exc)
            if (code := _http_status_code(item)) is not None
        ),
        None,
    )
    if status_code == 409:
        return RuntimeFailure(
            disposition=FailureDisposition.TERMINAL,
            error_code="runtime.http_409",
            message="任务因业务冲突未能继续，请处理后重试",
            recovery_action="请刷新任务状态，处理冲突后重新提交。",
        )
    if disposition is FailureDisposition.RETRYABLE:
        return RuntimeFailure(
            disposition=disposition,
            error_code="runtime.retryable",
            message="服务暂时不可用，系统将自动重试。",
            recovery_action="请稍候，系统会自动重试本次任务。",
        )
    if status_code is not None:
        return RuntimeFailure(
            disposition=disposition,
            error_code=f"runtime.http_{status_code}",
            message="任务当前无法继续，请检查任务状态后重试。",
            recovery_action="请检查任务状态和访问权限后重新提交。",
        )
    if any(isinstance(item, ValidationError) for item in exception_chain(exc)):
        return RuntimeFailure(
            disposition=disposition,
            error_code="runtime.validation",
            message="任务参数不符合要求，无法继续执行。",
            recovery_action="请检查任务信息后重新提交。",
        )
    return RuntimeFailure(
        disposition=disposition,
        error_code="runtime.terminal",
        message="任务未能继续执行，请检查配置后重试。",
        recovery_action="请检查任务配置、权限和可用资源后重新提交。",
    )


_RETRYABLE_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    httpx.NetworkError,
    httpx.TimeoutException,
)


def _http_status_code(exc: BaseException) -> int | None:
    if isinstance(exc, HTTPException):
        return exc.status_code
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    return None
