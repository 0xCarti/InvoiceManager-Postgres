(function (window, document) {
  'use strict';

  const EXPRESSION_ALLOWED_RE = /^[0-9+\-*/().\s]+$/;
  const PLAIN_NUMBER_RE = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/;
  const DATE_FORMAT_PATTERNS = [
    /^\d{4}[-/]\d{2}[-/]\d{2}$/,
    /^\d{1,2}[-/]\d{1,2}[-/]\d{4}$/,
  ];

  const OPERATOR_PRECEDENCE = {
    '+': 1,
    '-': 1,
    '*': 2,
    '/': 2,
  };

  function isOperator(token) {
    return token === '+' || token === '-' || token === '*' || token === '/';
  }

  function isDigit(char) {
    return char >= '0' && char <= '9';
  }

  function isNumericStart(char) {
    return char === '.' || isDigit(char);
  }

  function parseNumberToken(text, start) {
    let index = start;
    let sawDigit = false;
    let sawDecimal = false;
    const length = text.length;
    while (index < length) {
      const char = text.charAt(index);
      if (isDigit(char)) {
        sawDigit = true;
        index += 1;
      } else if (char === '.') {
        if (sawDecimal) {
          break;
        }
        sawDecimal = true;
        index += 1;
      } else {
        break;
      }
    }
    if (!sawDigit) {
      return null;
    }
    const numericText = text.slice(start, index);
    const numericValue = Number(numericText);
    if (!Number.isFinite(numericValue)) {
      return null;
    }
    return { value: numericValue, nextIndex: index };
  }

  function tokenizeExpression(expression) {
    const tokens = [];
    const length = expression.length;
    let index = 0;
    let previousType = 'start';

    while (index < length) {
      const char = expression.charAt(index);
      if (char === ' ' || char === '\t' || char === '\n' || char === '\r') {
        index += 1;
        continue;
      }
      if (isNumericStart(char)) {
        const numberToken = parseNumberToken(expression, index);
        if (!numberToken) {
          return null;
        }
        tokens.push({ type: 'number', value: numberToken.value });
        index = numberToken.nextIndex;
        previousType = 'number';
        continue;
      }
      if (char === '+' || char === '-') {
        const isUnary =
          previousType === 'start' ||
          previousType === 'operator' ||
          previousType === 'open_paren';
        if (isUnary) {
          let lookahead = index + 1;
          while (
            lookahead < length &&
            (expression.charAt(lookahead) === ' ' ||
              expression.charAt(lookahead) === '\t' ||
              expression.charAt(lookahead) === '\n' ||
              expression.charAt(lookahead) === '\r')
          ) {
            lookahead += 1;
          }
          if (lookahead < length && isNumericStart(expression.charAt(lookahead))) {
            const numberToken = parseNumberToken(expression, lookahead);
            if (!numberToken) {
              return null;
            }
            const signedValue =
              char === '-' ? -numberToken.value : numberToken.value;
            tokens.push({ type: 'number', value: signedValue });
            index = numberToken.nextIndex;
            previousType = 'number';
            continue;
          }
          if (char === '+') {
            index += 1;
            previousType = 'operator';
            continue;
          }
          if (char === '-' && lookahead < length && expression.charAt(lookahead) === '(') {
            tokens.push({ type: 'number', value: 0 });
            tokens.push({ type: 'operator', value: '-' });
            index += 1;
            previousType = 'operator';
            continue;
          }
          return null;
        }
        tokens.push({ type: 'operator', value: char });
        index += 1;
        previousType = 'operator';
        continue;
      }
      if (char === '*' || char === '/') {
        if (previousType !== 'number' && previousType !== 'close_paren') {
          return null;
        }
        tokens.push({ type: 'operator', value: char });
        index += 1;
        previousType = 'operator';
        continue;
      }
      if (char === '(') {
        tokens.push({ type: 'open_paren', value: char });
        index += 1;
        previousType = 'open_paren';
        continue;
      }
      if (char === ')') {
        if (previousType === 'operator' || previousType === 'open_paren' || previousType === 'start') {
          return null;
        }
        tokens.push({ type: 'close_paren', value: char });
        index += 1;
        previousType = 'close_paren';
        continue;
      }
      return null;
    }
    return tokens;
  }

  function toRpn(tokens) {
    const output = [];
    const operators = [];
    for (let i = 0; i < tokens.length; i += 1) {
      const token = tokens[i];
      if (token.type === 'number') {
        output.push(token);
      } else if (token.type === 'operator') {
        while (operators.length > 0) {
          const top = operators[operators.length - 1];
          if (
            top.type === 'operator' &&
            OPERATOR_PRECEDENCE[top.value] >= OPERATOR_PRECEDENCE[token.value]
          ) {
            output.push(operators.pop());
          } else {
            break;
          }
        }
        operators.push(token);
      } else if (token.type === 'open_paren') {
        operators.push(token);
      } else if (token.type === 'close_paren') {
        let foundOpen = false;
        while (operators.length > 0) {
          const top = operators.pop();
          if (top.type === 'open_paren') {
            foundOpen = true;
            break;
          }
          output.push(top);
        }
        if (!foundOpen) {
          return null;
        }
      }
    }
    while (operators.length > 0) {
      const token = operators.pop();
      if (token.type === 'open_paren' || token.type === 'close_paren') {
        return null;
      }
      output.push(token);
    }
    return output;
  }

  function evaluateRpn(rpnTokens) {
    const stack = [];
    for (let i = 0; i < rpnTokens.length; i += 1) {
      const token = rpnTokens[i];
      if (token.type === 'number') {
        stack.push(token.value);
        continue;
      }
      if (!isOperator(token.value) || stack.length < 2) {
        return NaN;
      }
      const right = stack.pop();
      const left = stack.pop();
      let result;
      switch (token.value) {
        case '+':
          result = left + right;
          break;
        case '-':
          result = left - right;
          break;
        case '*':
          result = left * right;
          break;
        case '/':
          if (right === 0) {
            return NaN;
          }
          result = left / right;
          break;
        default:
          return NaN;
      }
      if (!Number.isFinite(result)) {
        return NaN;
      }
      stack.push(result);
    }
    if (stack.length !== 1) {
      return NaN;
    }
    return stack[0];
  }

  function evaluateExpression(expression) {
    if (typeof expression !== 'string') {
      return NaN;
    }
    const trimmed = expression.trim();
    if (!trimmed || !EXPRESSION_ALLOWED_RE.test(trimmed)) {
      return NaN;
    }
    const tokens = tokenizeExpression(trimmed);
    if (!tokens || tokens.length === 0) {
      return NaN;
    }
    const rpn = toRpn(tokens);
    if (!rpn || rpn.length === 0) {
      return NaN;
    }
    return evaluateRpn(rpn);
  }

  function looksLikeDate(text) {
    if (typeof text !== 'string') {
      return false;
    }
    const normalized = text.replace(/\s+/g, '');
    if (!normalized) {
      return false;
    }
    for (let i = 0; i < DATE_FORMAT_PATTERNS.length; i += 1) {
      if (DATE_FORMAT_PATTERNS[i].test(normalized)) {
        return true;
      }
    }
    return false;
  }

  function extractExpression(text) {
    if (typeof text !== 'string') {
      return null;
    }
    let trimmed = text.trim();
    if (!trimmed) {
      return null;
    }

    let hadLeadingEquals = false;
    if (trimmed.charAt(0) === '=') {
      hadLeadingEquals = true;
      trimmed = trimmed.slice(1).trim();
    }

    if (!trimmed) {
      return null;
    }

    if (looksLikeDate(trimmed)) {
      return null;
    }

    if (!EXPRESSION_ALLOWED_RE.test(trimmed)) {
      return null;
    }

    const compact = trimmed.replace(/\s+/g, '');
    if (!compact) {
      return null;
    }

    if (!hadLeadingEquals) {
      if (!/[+\-*/()]/.test(compact)) {
        return null;
      }
      if (PLAIN_NUMBER_RE.test(compact)) {
        return null;
      }
    }

    return trimmed;
  }

  function parseValue(value) {
    if (value instanceof window.HTMLInputElement) {
      value = value.value;
    }
    if (value === null || value === undefined) {
      return NaN;
    }
    const text = String(value);
    const trimmed = text.trim();
    if (!trimmed) {
      return NaN;
    }
    const expression = extractExpression(trimmed);
    if (expression !== null) {
      const evaluated = evaluateExpression(expression);
      if (Number.isFinite(evaluated)) {
        return evaluated;
      }
    }

    const normalized = trimmed.replace(/,/g, '');
    const numeric = Number(normalized);
    if (Number.isFinite(numeric)) {
      return numeric;
    }
    if (EXPRESSION_ALLOWED_RE.test(normalized)) {
      return NaN;
    }
    return NaN;
  }

  function parseOrDefault(value, defaultValue) {
    const parsed = parseValue(value);
    return Number.isFinite(parsed) ? parsed : defaultValue;
  }

  function getStepDecimalPlaces(input) {
    if (!input) {
      return null;
    }
    const stepAttr = input.getAttribute('step');
    if (!stepAttr) {
      return null;
    }
    const trimmed = stepAttr.trim();
    if (!trimmed || trimmed.toLowerCase() === 'any') {
      return null;
    }
    const decimalIndex = trimmed.indexOf('.');
    if (decimalIndex === -1) {
      return null;
    }
    const decimalPortion = trimmed.slice(decimalIndex + 1).replace(/[^0-9].*$/, '');
    return decimalPortion ? decimalPortion.length : null;
  }

  function formatResolvedValue(input, value) {
    if (!Number.isFinite(value)) {
      return '';
    }
    let rounded = value;
    const decimalPlaces = getStepDecimalPlaces(input);
    const places = Number.isInteger(decimalPlaces) ? decimalPlaces : 10;
    try {
      rounded = Number(value.toFixed(places));
    } catch (error) {
      rounded = value;
    }
    if (!Number.isFinite(rounded)) {
      rounded = value;
    }
    return rounded.toString();
  }

  function resolveExpressionForInput(input, options) {
    if (!(input instanceof window.HTMLInputElement)) {
      return;
    }
    const rawValue = input.value;
    if (typeof rawValue !== 'string') {
      return;
    }
    const expression = extractExpression(rawValue);
    if (expression === null) {
      return;
    }
    const result = evaluateExpression(expression);
    if (!Number.isFinite(result)) {
      return;
    }
    const formatted = formatResolvedValue(input, result);
    if (formatted === rawValue) {
      return;
    }
    input.value = formatted;
    const shouldDispatch = !options || options.dispatchEvents !== false;
    if (!shouldDispatch) {
      return;
    }

    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function handleInputBlur(event) {
    resolveExpressionForInput(event.target);
  }

  function handleInputChange(event) {
    resolveExpressionForInput(event.target);
  }

  function enableInput(input) {
    if (!input || input.dataset.numericExpressionEnabled === '1') {
      return;
    }
    const currentType = input.getAttribute('type');
    if (currentType && currentType.toLowerCase() === 'number') {
      input.setAttribute('data-original-type', currentType);
      try {
        input.type = 'text';
      } catch (error) {
        input.setAttribute('type', 'text');
      }
    }
    input.setAttribute('data-numeric-input', '1');

    if (!input.hasAttribute('inputmode')) {
      input.setAttribute('inputmode', 'decimal');
    }
    input.addEventListener('blur', handleInputBlur);
    input.addEventListener('change', handleInputChange);
    input.dataset.numericExpressionEnabled = '1';

    if (input.value && typeof input.value === 'string') {
      resolveExpressionForInput(input, { dispatchEvents: false });
    }
  }

  function enableWithin(root) {
    if (!root) {
      return;
    }
    if (root instanceof window.HTMLInputElement) {
      enableInput(root);
      return;
    }
    const inputs = root.querySelectorAll('input[type="number"], input[data-numeric-input]');
    inputs.forEach(enableInput);
  }

  document.addEventListener('DOMContentLoaded', function () {
    enableWithin(document);
  });

  if (document.readyState !== 'loading') {
    enableWithin(document);
  }

  document.addEventListener(
    'focusout',
    function (event) {
      const target = event.target;
      if (
        !target ||
        !(target instanceof window.HTMLInputElement) ||
        target.dataset.numericExpressionEnabled !== '1'
      ) {
        return;
      }
      resolveExpressionForInput(target);
    },
    true
  );

  const observer = new MutationObserver(function (mutations) {
    mutations.forEach(function (mutation) {
      mutation.addedNodes.forEach(function (node) {
        if (!(node instanceof window.Element)) {
          return;
        }
        if (node.matches('input[type="number"], input[data-numeric-input]')) {
          enableInput(node);
        } else {
          enableWithin(node);
        }
      });
    });
  });

  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
  });

  window.NumericInput = {
    enableWithin: enableWithin,
    parseValue: parseValue,
    parseOrDefault: parseOrDefault,
    evaluateExpression: evaluateExpression,
    resolveExpressionForInput: resolveExpressionForInput,
  };
})(window, document);
