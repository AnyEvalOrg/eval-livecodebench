"""Small renderer for this chart's Go-template subset when Helm is unavailable.

The pinned provider shells out to Helm and has no independent render helper.
Read actual templates and reject unsupported syntax rather than silently skipping
it. This validates chart structure, not Helm or Kubernetes admission semantics.
"""
import json
import re

import yaml


def render_chart(chart, values, release, namespace):
    context = {
        '.Values': values,
        '.Release': {'Name': release, 'Namespace': namespace},
        '.Chart': yaml.safe_load((chart / 'Chart.yaml').read_text()),
    }
    context['.Chart']['Name'] = context['.Chart']['name']

    def to_yaml(value):
        return yaml.safe_dump(value, sort_keys=False).removesuffix('...\n').rstrip()

    def render(source):
        # Declarations belong to this template and take effect in source order.
        variables = {}

        def lookup(expression):
            path = re.fullmatch(r'(\.[A-Za-z_]\w*|\$[A-Za-z_]\w*)((?:\.[A-Za-z_]\w*)*)', expression)
            if not path:
                raise AssertionError(f'Unsupported template expression: {expression}')
            root, tail = path.groups()
            scope = variables if root.startswith('$') else context
            if root not in scope:
                raise AssertionError(f'Undeclared variable or unknown path: {expression}')
            value = scope[root]
            for key in tail.split('.')[1:]:
                if not isinstance(value, dict) or key not in value:
                    raise AssertionError(f'Unknown template path: {expression}')
                value = value[key]
            return value

        # Helm's left/right whitespace trimming, before expression substitution.
        source = re.sub(r'\s*{{-', '{{', source)
        source = re.sub(r'-}}\s*', '}}', source)
        # Only the resource loop used by network-policy.yaml is supported.
        # Match its entire body, so even an empty range cannot hide bad syntax.
        resource_loop = r'{{\s*range \.Values.additionalResources\s*}}\n{{\s*tpl \(toYaml \.\) \$\s*}}\n---{{\s*end\s*}}'

        def substitute(match):
            if re.fullmatch(resource_loop, match[0]):
                resources = lookup('.Values.additionalResources')
                assert isinstance(resources, list), 'Expected additionalResources list'
                return ''.join('\n' + render(to_yaml(resource)) + '\n---' for resource in resources)
            expression = match[1].strip()
            declaration = re.fullmatch(r'(\$[A-Za-z_]\w*)\s*:=\s*(\S+)', expression)
            if declaration:
                variables[declaration[1]] = lookup(declaration[2])
                return ''
            if expression == 'toYaml (.Values.annotations | default dict) | nindent 4':
                value = to_yaml(values.get('annotations') or {})
                return '\n' + '\n'.join('    ' + line for line in value.splitlines())
            indent = re.fullmatch(r'toYaml (\S+) \| nindent (\d+)', expression)
            if indent:
                value = to_yaml(lookup(indent[1]))
                return '\n' + '\n'.join(' ' * int(indent[2]) + line for line in value.splitlines())
            if expression.endswith(' | toJson'):
                return json.dumps(lookup(expression.removesuffix(' | toJson')))
            value = lookup(expression)
            return str(value).lower() if isinstance(value, bool) else str(value)

        rendered = re.sub(resource_loop + r'|{{(.*?)}}', substitute, source, flags=re.S)
        assert '{{' not in rendered and '}}' not in rendered
        return rendered

    return '\n---\n'.join(render(path.read_text()) for path in sorted((chart / 'templates').glob('*.yaml')))
