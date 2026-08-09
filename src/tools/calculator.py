# 计算工具 - 优化版

import ast
import math
import operator
from typing import Optional
from pydantic import BaseModel, Field

from langchain_core.tools import tool


# ponytail: 计算结果结构化输出
class CalculationResult(BaseModel):
    """计算结果结构化输出"""
    expression: str = Field(description="原始表达式")
    result: float | int = Field(description="计算结果")
    success: bool = Field(description="是否成功")
    error: Optional[str] = Field(default=None, description="错误信息")


# ponytail: ast安全白名单——允许的运算符/函数, 其余节点(Name/Attribute/非白名单Call)一律拒, 防 LLM 注入
_ALLOWED_BINOPS = {ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv}
_ALLOWED_UNARYOPS = {ast.UAdd, ast.USub}
_ALLOWED_FUNCS = {"sqrt": math.sqrt, "abs": abs, "floor": math.floor, "ceil": math.ceil,
                  "log": math.log, "log10": math.log10, "sin": math.sin, "cos": math.cos}


def calculate(expression: str) -> str:
    """安全计算数学表达式(ast白名单求值, 禁eval). 失败返回错误提示串(不抛)."""
    expr = (expression or "").strip()
    if not expr or len(expr) > 200:
        return "(表达式为空或过长)"
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return f"(表达式语法错误: {expr})"

    _binop_map = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                  ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
                  ast.FloorDiv: operator.floordiv}

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
            left, right = _eval(node.left), _eval(node.right)
            if isinstance(node.op, ast.Div) and right == 0:
                raise ZeroDivisionError
            return _binop_map[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
            v = _eval(node.operand)
            return v if isinstance(node.op, ast.UAdd) else -v
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in _ALLOWED_FUNCS and node.args and not node.keywords:
            return _ALLOWED_FUNCS[node.func.id](*[_eval(a) for a in node.args])
        raise ValueError(f"不允许的节点: {type(node).__name__}")

    try:
        result = _eval(tree)
        if isinstance(result, float) and (math.isinf(result) or math.isnan(result)):
            return "(计算结果无效)"
        return f"{expr} = {result}"
    except ZeroDivisionError:
        return "(除零错误)"
    except Exception as e:
        return f"(无法计算: {type(e).__name__})"


@tool
def calc_tool(expression: str) -> str:
    """精确计算数学表达式(四则运算、幂、括号、数学函数)。

    支持的运算符: +, -, *, /, ** (幂), % (取余), // (整除)
    支持的函数: sqrt, abs, floor, ceil, log, log10, sin, cos
    安全特性: 使用AST白名单求值，防止代码注入

    Args:
        expression: 要计算的数学表达式，如 "2+3*4" 或 "sqrt(16)"

    Returns:
        str: 计算结果，格式为 "表达式 = 结果"，或错误信息
    """
    return calculate(expression)


__all__ = ["calculate", "calc_tool", "CalculationResult"]
