from uuid import UUID

from temnia_pipeline.activities import say_hello
from temnia_pipeline.contracts import HelloInput, Scope

SEEDED = Scope(
    organizationId=UUID("0192e8a0-0000-7000-8000-000000000001"),
    userId=UUID("0192e8a0-0000-7000-8000-000000000002"),
)


async def test_say_hello_echoes_the_scope_organization() -> None:
    result = await say_hello(HelloInput(scope=SEEDED, name="Rajesh"))
    assert result.organizationId == SEEDED.organizationId
    assert result.workerLanguage == "python"
    assert "Rajesh" in result.greeting
    assert result.workerHost
