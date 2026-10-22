import inspect

import core.repositories.clients as clients
import core.repositories.meetings as meetings
import core.repositories.messages as messages
import core.repositories.operator_status as operator_status
import core.repositories.operators as operators


def test_repositories_do_not_decrypt() -> None:
    for module in (operators, clients, messages, meetings, operator_status):
        source = inspect.getsource(module)
        assert "decrypt_for_operator" not in source
        assert "decrypt_for_desk" not in source
        assert "decrypt_profile" not in source
