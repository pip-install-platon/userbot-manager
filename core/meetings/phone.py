def normalize_phone(value: str | None, *, strict: bool = True) -> str | None:
    if value is None:
        return None
    compact = "".join(ch for ch in value if ch.isdigit() or ch == "+")
    if not compact:
        return None
    try:
        _validate_phone(compact)
    except ValueError:
        if strict:
            raise
        return None
    return compact


def _validate_phone(compact: str) -> None:
    if compact.count("+") > 1 or ("+" in compact and not compact.startswith("+")):
        raise ValueError("укажите номер цифрами, можно с плюсом в начале")
    digits = compact[1:] if compact.startswith("+") else compact
    if not digits.isdigit() or not 5 <= len(digits) <= 16:
        raise ValueError("укажите номер цифрами, можно с плюсом в начале")
