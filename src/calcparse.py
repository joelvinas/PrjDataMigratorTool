#!/usr/bin/env python3
"""Parser for Airtable formulas (the calculated fields feature). Standard library only. Plain recursive descent over the tokens from calclex.py: deterministic, no network,
no model, no randomness (a test checks the imports).

    parse(text) -> a tree of nodes             raises ParseError (with a character position) when the text is not a formula this parser knows

Nodes are tuples, so a tree compares and prints simply:
    ("num", "0.15")   ("str", "|")   ("ref", "fldXXX", "id" | "name")   ("ident", "TRUE")   ("call", "IF", [args])   ("bin", "+", left, right)   ("neg", operand)

Precedence, lowest to highest (Airtable's own order is provisional until compared against its output): || , && , comparisons (= != <> < > <= >=) , & , + - , * / , unary minus.
Binary operators group left to right. An error message gives a position and never repeats the formula.
"""
import calclex as cl

PREC = {"||": 1, "&&": 2, "=": 3, "!=": 3, "<>": 3, "<": 3, ">": 3, "<=": 3, ">=": 3, "&": 4, "+": 5, "-": 5, "*": 6, "/": 6}
UNARY_PREC = 7
MAX_DEPTH = 60


class ParseError(ValueError):
    def __init__(self, message, pos):
        ValueError.__init__(self, "%s (at character %d)" % (message, pos))
        self.pos = pos


class _Parser(object):
    def __init__(self, tokens, end):
        self.t, self.i, self.end, self.depth = tokens, 0, end, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def pos(self):
        tok = self.peek()
        return tok.pos if tok else self.end

    def take(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def expression(self, min_prec):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ParseError("the formula is nested too deeply", self.pos())
        left = self.unary()
        while True:
            tok = self.peek()
            if tok is None or tok.kind != cl.OP or tok.value not in PREC or PREC[tok.value] < min_prec:
                break
            self.take()
            right = self.expression(PREC[tok.value] + 1)
            left = ("bin", tok.value, left, right)
        self.depth -= 1
        return left

    def unary(self):
        tok = self.peek()
        if tok is not None and tok.kind == cl.OP and tok.value in ("-", "+"):
            self.take()
            operand = self.expression(UNARY_PREC)
            return ("neg", operand) if tok.value == "-" else operand
        return self.primary()

    def primary(self):
        tok = self.peek()
        if tok is None:
            raise ParseError("the formula ends where a value was expected", self.end)
        if tok.kind == cl.NUMBER:
            self.take()
            return ("num", tok.value)
        if tok.kind == cl.STRING:
            self.take()
            return ("str", tok.value)
        if tok.kind == cl.FIELD:
            self.take()
            return ("ref", tok.value, tok.ref_kind)
        if tok.kind == cl.IDENT:
            self.take()
            return ("ident", tok.value)
        if tok.kind == cl.LPAREN:
            self.take()
            inner = self.expression(1)
            self.close(")")
            return inner
        if tok.kind == cl.FUNC:
            self.take()
            self.take_kind(cl.LPAREN, "(")
            args = []
            nxt = self.peek()
            if nxt is not None and nxt.kind == cl.RPAREN:
                self.take()
                return ("call", tok.value, args)
            while True:
                args.append(self.expression(1))
                nxt = self.peek()
                if nxt is not None and nxt.kind == cl.COMMA:
                    self.take()
                    continue
                self.close(")")
                return ("call", tok.value, args)
        raise ParseError("unexpected %s" % tok.kind.lower(), tok.pos)

    def take_kind(self, kind, text):
        tok = self.peek()
        if tok is None or tok.kind != kind:
            raise ParseError("expected %s" % text, self.pos())
        return self.take()

    def close(self, text):
        self.take_kind(cl.RPAREN, text)


def parse(text):
    try:
        tokens = cl.lex(text)
    except cl.LexError as e:
        raise ParseError(str(e), e.pos)
    if not tokens:
        raise ParseError("the formula is empty", 0)
    p = _Parser(tokens, tokens[-1].end)
    tree = p.expression(1)
    if p.peek() is not None:
        raise ParseError("unexpected %s after the end of the formula" % p.peek().kind.lower(), p.peek().pos)
    return tree


def walk(node):
    """Every node of a tree, parents before children, left to right."""
    yield node
    if node[0] == "call":
        for a in node[2]:
            for x in walk(a):
                yield x
    elif node[0] == "bin":
        for x in walk(node[2]):
            yield x
        for x in walk(node[3]):
            yield x
    elif node[0] == "neg":
        for x in walk(node[1]):
            yield x
