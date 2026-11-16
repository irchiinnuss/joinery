"""Return anticipated tool failures with useful messages, without exposing tracebacks.

No failure reaches the agent as a blank message: an exception without text (a
TimeoutError is an OSError and ``str(TimeoutError())`` is empty) is said in
words by ``joinery.errors.said``, and anything unanticipated names its type.
"""

from collections.abc import Awaitable, Callable
from functools import wraps

from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from joinery.errors import error_text, said
from joinery.mcp_runtime import PolicyStop


def tool_errors[**P, T](
    function: Callable[P, Awaitable[T]],
) -> Callable[P, Awaitable[T]]:
    what = f"The tool {function.__name__}"

    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return await function(*args, **kwargs)
        except PolicyStop as exc:
            raise ToolError("Rule Zero STOP: " + said(exc, what)) from exc
        except MCPError:
            raise
        except ToolError as exc:
            if str(exc).strip():
                raise
            raise ToolError(said(exc, what)) from exc
        except (ValueError, RuntimeError, OSError) as exc:
            raise ToolError(said(exc, what)) from exc
        except Exception as exc:
            # Unanticipated, but still said: a blank error tells the agent nothing,
            # so it repeats the same call.
            raise ToolError(
                f"{what} failed inside Joinery ({error_text(exc)})."
            ) from exc

    return wrapped
