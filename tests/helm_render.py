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
        '$service': values['services']['default'],
    }
    context['.Chart']['Name'] = context['.Chart']['name']

    def lookup(expression):
        for prefix, value in context.items():
            if expression == prefix or expression.startswith(prefix + '.'):
                for key in expression[len(prefix):].strip('.').split('.'):
                    if key:
                        value = value[key]
                return value
        raise AssertionError(f'Unsupported template expression: {expression}')

    def to_yaml(value):
        return yaml.safe_dump(value, sort_keys=False).removesuffix('...\n').rstrip()

    def render(source):
        # Helm's left/right whitespace trimming, before expression substitution.
        source = re.sub(r'\s*{{-', '{{', source)
        source = re.sub(r'-}}\s*', '}}', source)
        source = source.replace('{{ $service := .Values.services.default }}', '')
        source = re.sub(
            r'{{ range \.Values.additionalResources }}(.*?){{ end }}',
            lambda match: ''.join(
                render(match[1].replace('{{ tpl (toYaml .) $ }}', to_yaml(resource)))
                for resource in values['additionalResources']
            ), source, flags=re.S,
        )

        def substitute(match):
            expression = match[1].strip()
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

        rendered = re.sub(r'{{(.*?)}}', substitute, source, flags=re.S)
        assert '{{' not in rendered and '}}' not in rendered
        return rendered

    return '\n---\n'.join(render(path.read_text()) for path in sorted((chart / 'templates').glob('*.yaml')))
