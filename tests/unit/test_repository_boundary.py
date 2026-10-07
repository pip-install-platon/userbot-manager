import inspect

import core.repositories.clients as clients
import core.repositories.messages as messages
import core.repositories.operators as operators


def test_repositories_do_not_decrypt() -> None:
    for module in (operators, clients, messages):
        source = inspect.getsource(module)
        assert "decrypt_for_operator" not in source
        assert "decrypt_profile" not in source
