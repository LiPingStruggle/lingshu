# -*- coding: utf-8 -*-
"""Parser module for LingShu tests"""


class ParserError(Exception):
    """Parser 异常"""
    pass


def parse_input(text: str) -> list[str]:
    """
    解析输入字符串，返回 token 列表。

    处理特殊字符、unicode、空字符串等边界情况。
    """
    if not text or not text.strip():
        return []

    # 过滤非法 Unicode 和控制字符（除常见空白符外）
    filtered_chars = []
    for ch in text:
        # 允许：可打印字符、常见空白符（空格、制表符、换行符、回车符）
        if (
            ch.isprintable() 
            or ch in '\t\n\r '  # 允许制表符、换行符、回车符和空格
            or (0 <= ord(ch) < 32 and ch in '\t\n\r')  # 仅允许特定控制字符
        ):
            filtered_chars.append(ch)
        # 其他控制字符（如 \x00-\x08, \x0b, \x0c, \x0e-\x1f）将被过滤掉
    
    cleaned_text = "".join(filtered_chars)

    tokens = []
    current = []
    in_quote = False
    quote_char = None

    for ch in cleaned_text:
        if in_quote:
            current.append(ch)
            if ch == quote_char:
                tokens.append("".join(current))
                current = []
                in_quote = False
                quote_char = None
        elif ch in ('"', "'", '“', '”', '「', '」'):
            if current:
                tokens.append("".join(current))
                current = []
            current.append(ch)
            in_quote = True
            quote_char = ch
        elif ch.isspace():
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(ch)

    if current:
        tokens.append("".join(current))

    # 过滤空 token
    tokens = [t for t in tokens if t.strip()]
    return tokens


def safe_parse(text: str) -> list[str]:
    """安全解析入口，所有异常包装为 ParserError"""
    try:
        return parse_input(text)
    except Exception as e:
        raise ParserError(f"Parse failed: {e}") from e


def validate_encoding(text: str) -> bool:
    """验证字符串编码是否正确"""
    try:
        text.encode("utf-8").decode("utf-8")
        return True
    except Exception:
        return False
