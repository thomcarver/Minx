import sys
import string
import optparse
import logging
import os
from Lexer import *

PARSED_STRING = 0 # string contains names to match
PARSED_CASE = 1  # expression, [branchpattern1, branchpattern2, branchexp], elseExp
PARSED_NAME = 2  # string, hasSideEffects, isMutable, isInfix
PARSED_DOLLAR = 3
PARSED_SCOPE = 4  # [name or scope, declarationType, expression]
PARSED_UNION_TYPE = 5 # [expression]
PARSED_APPLICATION = 6 # expression1, expression2
PARSED_MEMBER_ACCESS = 7 # expression, Name

# TODO
PARSED_META = 9   # expression

def tryParseOne(tokenSource, parserList):
    for parser in parserList:
        parsed = parser(tokenSource)
        if parsed != None:
            return parsed

def tryParseMeta(tokenSource):
    if tokenSource.isNextToken(TOKEN_SINGLEQUOTE):
        expression = tryParseExpression(tokenSource)
        if expression == None:
            tokenSource.error("expected expression between single quotes for meta ")

        if tokenSource.isNextToken(TOKEN_SINGLEQUOTE) == False:
            tokenSource.error("expected closing single quote for meta")

        return (PARSED_META, expression)

def tryParseGroup(tokenSource):
    if tokenSource.isNextToken(TOKEN_OPEN_PARENTHESES):
        expression = tryParseUnion(tokenSource)
        if expression == None:
            tokenSource.error("expected expression between parentheses \"()\"")

        if tokenSource.isNextToken(TOKEN_CLOSE_PARENTHESES) == False:
            tokenSource.error("expected closing parenthesis \")\"")

        return expression

def tryParseCase(tokenSource):
    if tokenSource.isNextToken(TOKEN_CASE):
        exp = tryParseExpression(tokenSource)
        if exp == None:
            tokenSource.error("expected expression as starting point for case statement")
        logging.debug("parsed case generator expression: {0}".format(repr(exp)))

        branches = []
        elseBranch = None
        pipesAreIndented = tokenSource.isNextToken(TOKEN_INDENT)

        while tokenSource.isNextToken(TOKEN_PIPE):
            if elseBranch != None:
                tokenSource.error("An else branch must be the last branch of a case statement.")
            isElse = False
            branchPattern = tryParseOne(tokenSource, [tryParseExplicitScope, tryParseName])
            branchPattern_type = None
            if branchPattern == None:
                isElse = tokenSource.isNextToken(TOKEN_ELSE)
                if isElse == False:
                    tokenSource.error("expected pattern for this branch of the case statement")

            elif branchPattern[0] == PARSED_NAME:
                branchPattern_type = tryParseName(tokenSource)

            logging.debug("parsed case branch pattern: {0}, type {1}".format(repr(branchPattern), repr(branchPattern_type)))

            if tokenSource.isNextToken(TOKEN_COLON) == False:
                tokenSource.error("expected \":\" after pattern in case branch.")

            branchExp = tryParseOne(tokenSource, [tryParseImplicitScope, tryParseExpression])
            if branchExp == None:
                tokenSource.error("expected expression for this branch of the case statement")
            logging.debug("parsed case branch expression: {0}".format(repr(branchExp)))

            if isElse:
                elseBranch = branchExp
            else:
                branches.append((branchPattern, branchPattern_type, branchExp))
            if pipesAreIndented:
                if tokenSource.isNextToken(TOKEN_UNINDENT):
                    break
                elif tokenSource.isNextToken(TOKEN_NEWLINE) == False:
                    tokenSource.error("expected indentation matching first branch in the case statement")
        
        if len(branches) == 0:
            tokenSource.error("case statements require at least one non-else branch (expected |)")

        return (PARSED_CASE, exp, branches, elseBranch)




def tryParseBaseExpression(tokenSource):
    mainParsers = [
      tryParseDollar, 
      tryParseGroup, 
      tryParseMeta, 
      tryParseExplicitScope, 
      tryParseList, 
      tryParseCase,
      tryParseName,
      tryParseString]

    return tryParseOne(tokenSource, mainParsers)

def tryParseMemberAccess(tokenSource):
    expression = tryParseBaseExpression(tokenSource)
    if expression == None:
        return None

    while tokenSource.isNextToken(TOKEN_AT):
        member = tryParseOne(tokenSource, [tryParseName])
        if member == None:
            tokenSource.error("expected member name after member-access character \"@\"")
        expression = (PARSED_MEMBER_ACCESS, expression, member)

    return expression

class DoublyLinkedList():
    def __init__(self, item, isSentinel = False):
        self.left = None
        self.right = None
        self.item = item
        self.isSentinel = isSentinel

    def insertToLeft(self, node):
        if node != None:
            node.left = self.left
            if self.left != None:
                self.left.right = node
            node.right = self
            self.left = node

    def remove(self):
        if self.left != None:
            self.left.right = self.right
        if self.right != None:
            self.right.left = self.left
        self.left = None
        self.right = None

opsInOrder = "^*/%+-:><=&|"

def greatestPrecedence(currentBestNode, newNode):
    if currentBestNode == None:
        return newNode
    
    currentInfix = currentBestNode.item[1]
    newInfix = newNode.item[1]

    n1 = len(currentInfix)
    n2 = len(newInfix)
    for i in range(min(n1, n2)):
        prec1 = opsInOrder.find(currentInfix[i])
        prec2 = opsInOrder.find(newInfix[i])
        if prec2 < prec1:
            return newNode
        if prec1 < prec2:
            return currentBestNode

    return newNode if n1 > n2 else currentBestNode 

# TODO this runs in n^2 at the mo, but premature optimization, right?
def tryParseApplication(tokenSource):

    leftSentinel = DoublyLinkedList(None, True)
    rightSentinel = DoublyLinkedList(None, True)
    rightSentinel.insertToLeft(leftSentinel)

    exp = tryParseMemberAccess(tokenSource)
    while exp != None:
        expNode = DoublyLinkedList(exp)
        rightSentinel.insertToLeft(expNode)
        exp = tryParseMemberAccess(tokenSource)

    # first collapse infix operators
    while True:
        nextNode = leftSentinel.right
        highestOpNode = None
        while nextNode.isSentinel == False:
            if nextNode.item[0] == PARSED_NAME and nextNode.item[4]:
                highestOpNode = greatestPrecedence(highestOpNode, nextNode)
            nextNode = nextNode.right

        if highestOpNode != None:
            leftOperand = None
            rightOperand = None
            if highestOpNode.left.isSentinel == False:
                leftOperand = highestOpNode.left.item
                highestOpNode.left.remove()
            if highestOpNode.right.isSentinel == False:
                rightOperand = highestOpNode.right.item
                highestOpNode.right.remove()
            # 1+lhs => {!lhs = 1, !rhs= lhs, !result = (+) {lhs = !lhs, rhs = !rhs} }@!result

            highestOpNode.item = (PARSED_MEMBER_ACCESS, 
                (PARSED_SCOPE, [
                    ((PARSED_NAME, "!lhs", False, False, False), None, leftOperand),
                    ((PARSED_NAME, "!rhs", False, False, False), None, rightOperand),
                    ((PARSED_NAME, "!result", False, False, False),None,
                        (PARSED_APPLICATION, 
                            highestOpNode.item,
                            (PARSED_SCOPE, [
                                ((PARSED_NAME, "lhs", False, False, False),None,(PARSED_NAME, "!lhs", False, False, False)),
                                ((PARSED_NAME, "rhs", False, False, False),None,(PARSED_NAME, "!rhs", False, False, False))])
                        )
                     )]
                ), 
                (PARSED_NAME, "!result", False, False, False))
        else:
            break

    # last collapse function applications
    penultimateNode = rightSentinel.left
    if penultimateNode.isSentinel:
        return

    while penultimateNode.left.isSentinel == False:
        function = penultimateNode.left.item
        penultimateNode.left.remove()
        penultimateNode.item = (PARSED_APPLICATION, function, penultimateNode.item)

    return penultimateNode.item

def tryParseExpression(tokenSource):
    expression = tryParseApplication(tokenSource)
    if expression == None:
        return None

    # "as" is an inverted function application, "arguments as function"
    while tokenSource.isNextToken(TOKEN_AS):
        function = tryParseApplication(tokenSource)
        if function == None:
            tokenSource.error("expected cast after \"as\"")
        expression = (PARSED_APPLICATION, function, expression)

    return expression

def tryParseUnion(tokenSource): 
    exp = tryParseExpression(tokenSource)

    if exp == None:
        return None
    expressions = [exp]

    while tokenSource.isNextToken(TOKEN_PIPE):
        exp = tryParseExpression(tokenSource)
        if exp == None:
            tokenSource.error("expected expression for next type in union after \"|\"")
        expressions.append(exp)

    if len(expressions) > 1:
        return (PARSED_UNION_TYPE, expressions)
    return expressions[0]

def tryParseString(tokenSource):
    token = tokenSource.getIfOfType(TOKEN_STRING)
    if token != None:
        mappedNames = [(PARSED_NAME, name[1], name[2], name[3], name[0] == TOKEN_INFIX) for name in token[2]]
        return (PARSED_STRING, token[1], mappedNames)

def tryParseDollar(tokenSource):
    return (PARSED_DOLLAR, ) if tokenSource.isNextToken(TOKEN_DOLLAR) else None

def tryParseName(tokenSource):
    token = tokenSource.getIfOfType(TOKEN_NAME)
    isInfix = False
    if token == None:
        token = tokenSource.getIfOfType(TOKEN_INFIX)
        isInfix = True

    return (PARSED_NAME, token[1], token[2], token[3], isInfix) if token != None else None

def tryParseList(tokenSource):
    if tokenSource.isNextToken(TOKEN_OPEN_BRACKET):
        contents = []

        atEnd = tokenSource.isNextToken(TOKEN_CLOSE_BRACKET)
        while atEnd == False:
            exp = tryParseUnion(tokenSource)
            if exp == None:
                tokenSource.error('Expected expression in list definition') 

            contents.append(exp)

            if tokenSource.isNextToken(TOKEN_COMMA) == False:
                atEnd = tokenSource.isNextToken(TOKEN_CLOSE_BRACKET)
                if atEnd == False:
                    tokenSource.error('Expected end bracket (\"]\") for list end or comma for item separation.')

        # list = `empty_list | {hd, tl list}
        # ALIASING, e.g. [hd,hd,tl] must work correctly
        # [3,hd,4,tl] => {!0=3, !1=hd, !2=4, !3=tl, !result= {hd=!0, tl={hd=!1, tl={hd=!2, tl={hd=!3, tl=`empty_list}}}}}@!result
        tail = (PARSED_NAME, "`empty_list", False, False, False)
        if len(contents) == 0:
            return tail
        else:
            args = [((PARSED_NAME, "!" + str(i), False, False, False), None, contents[i]) for i in range(len(contents))]
            resultName = (PARSED_NAME, "!result", False, False, False)
            for i in reversed(range(len(contents))):
                tail = (PARSED_SCOPE, [
                    ((PARSED_NAME, "hd", False, False, False),None,(PARSED_NAME, "!" + str(i), False, False, False)),
                    ((PARSED_NAME, "tl", False, False, False),None,tail)])
            args.append((resultName, None, tail))
            return (PARSED_MEMBER_ACCESS, (PARSED_SCOPE, args), resultName)


tryParseExplicitScope = lambda tokenSource: tryParseScope(tokenSource, TOKEN_OPEN_BRACE, TOKEN_COMMA, TOKEN_CLOSE_BRACE)
tryParseImplicitScope = lambda tokenSource: tryParseScope(tokenSource, TOKEN_INDENT, TOKEN_NEWLINE, TOKEN_UNINDENT)
tryParseWholeFileScope = lambda tokenSource: tryParseScope(tokenSource, TOKEN_FILESTART, TOKEN_NEWLINE, TOKEN_FILEEND)

def tryParseScope(tokenSource, startToken, separatorToken, endToken):
    if tokenSource.isNextToken(startToken):
        logging.debug("found scope start token: {0}".format(startToken))
        scopeDeclarations = []

        nameParsers = [tryParseExplicitScope, tryParseName]

        atEnd = tokenSource.isNextToken(endToken)
        while atEnd == False:
            declaration = tryParseOne(tokenSource, nameParsers)
            if declaration == None:
                tokenSource.error('Expected name declaration in scope definition') 
                
            logging.debug("parsed declaration: {0}".format(repr(declaration)))
            declaration_type = None
            if declaration[0] == PARSED_NAME:
                declaration_type = tryParseUnion(tokenSource)
                logging.debug("parsed declaration type: {0}".format(repr(declaration_type)))

            value = None
            if tokenSource.isNextToken(TOKEN_EQUALS):
                logging.debug("found equals sign")
                value = tryParseOne(tokenSource, [tryParseImplicitScope, tryParseUnion])
                logging.debug("parsed value: {0}".format(repr(value)))
                if value == None:
                    tokenSource.error('Expected value after equals sign in scope declaration') 

            scopeDeclarations.append((declaration, declaration_type, value))

            if tokenSource.isNextToken(separatorToken) == False:
                atEnd = tokenSource.isNextToken(endToken)
                if atEnd == False:
                    tokenSource.error('Expected scope end or comma for member separation.')

        return (PARSED_SCOPE, scopeDeclarations)
