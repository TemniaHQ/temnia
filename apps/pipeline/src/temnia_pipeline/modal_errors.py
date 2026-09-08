"""Which Modal SDK exceptions mean "could not ask", shared by both adapters.

`FunctionCall.get` raises the function's own exception when the call failed,
re-raised with its type, which is a verdict on the run. It also raises when
the question never reached Modal: a dropped connection, a token the API
refused, a gRPC stream that ended, a socket error. Those say nothing about the
run, which may be healthy on its GPU, and an adapter that reported them as a
failed run made the runner start a second one beside it (S2 review, I05).
The import is inside the function so a worker on the local backends never
loads the SDK.
"""

from __future__ import annotations


def transport_errors() -> tuple[type[BaseException], ...]:
    """The exception types that mean the provider could not be reached."""
    from grpclib.exceptions import GRPCError, StreamTerminatedError  # noqa: PLC0415
    from modal.exception import AuthError, ClientClosed  # noqa: PLC0415
    from modal.exception import ConnectionError as ModalConnectionError  # noqa: PLC0415

    return (
        ModalConnectionError,
        AuthError,
        ClientClosed,
        GRPCError,
        StreamTerminatedError,
        OSError,
    )
