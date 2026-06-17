def make_code(letters: str, digits: int | str) -> str:
    letters = letters.upper().strip()

    if len(letters) != 3 or not letters.isalpha():
        raise ValueError("letters must contain exactly 3 letters")

    digits_str = str(digits).strip()

    if not digits_str.isdigit() or len(digits_str) > 4:
        raise ValueError("digits must contain from 1 to 4 digits")

    return f"{letters}_{digits_str.zfill(4)}"
