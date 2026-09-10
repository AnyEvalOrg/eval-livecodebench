"""Mutation tests for the deliberately limited Helm-free renderer."""
from pathlib import Path

import pytest
import yaml

from helm_render import render_chart


CHART = Path(__file__).resolve().parents[1] / 'livecodebench/chart'
DECLARATION = '{{- $service := .Values.services.default }}'


def render_mutation(monkeypatch, mutate, values=None):
    read_text = Path.read_text

    def template_text(path, *args, **kwargs):
        source = read_text(path, *args, **kwargs)
        return mutate(path.name, source) if path.parent == CHART / 'templates' else source

    monkeypatch.setattr(Path, 'read_text', template_text)
    if values is None:
        values = yaml.safe_load((CHART.parent / 'values.yaml').read_text())
    return render_chart(CHART, values, 'fixture', 'sandbox')


@pytest.mark.parametrize('replacement, error', [
    ('', 'Undeclared variable'),
    ('{{ $service.image }}' + DECLARATION, 'Undeclared variable'),
    ('{{- $service := .Values.services.missing }}', 'Unknown template path'),
    ('{{- $service := $missing }}', 'Undeclared variable'),
    (DECLARATION + '{{ $service.missing }}', 'Unknown template path'),
    (DECLARATION + '{{ .Values.services.default.image.missing }}', 'Unknown template path'),
    (DECLARATION + '{{ .Unknown }}', 'unknown path'),
    (DECLARATION + '{{ $service.image | quote }}', 'Unsupported template expression'),
])
def test_rejects_invalid_declarations_and_references(monkeypatch, replacement, error):
    with pytest.raises(AssertionError, match=error):
        render_mutation(monkeypatch, lambda name, source: source.replace(DECLARATION, replacement))


def test_declaration_name_and_value_come_from_template(monkeypatch):
    values = yaml.safe_load((CHART.parent / 'values.yaml').read_text())
    values['services']['alternate'] = {**values['services']['default'], 'image': 'fixture:alternate'}
    rendered = render_mutation(
        monkeypatch,
        lambda name, source: source.replace('$service', '$chosen').replace(
            ':= .Values.services.default', ':= .Values.services.alternate'),
        values,
    )
    pod = next(r for r in yaml.safe_load_all(rendered) if r and r['kind'] == 'Pod')
    assert pod['spec']['containers'][0]['image'] == 'fixture:alternate'


def test_declarations_do_not_leak_between_templates(monkeypatch):
    def mutate(name, source):
        return DECLARATION + source if name == 'network-policy.yaml' else source.replace(DECLARATION, '')

    with pytest.raises(AssertionError, match='Undeclared variable'):
        render_mutation(monkeypatch, mutate)


def test_empty_range_cannot_hide_unsupported_syntax(monkeypatch):
    values = yaml.safe_load((CHART.parent / 'values.yaml').read_text())
    values['additionalResources'] = []
    with pytest.raises(AssertionError, match='Unsupported template expression'):
        render_mutation(monkeypatch, lambda name, source: source.replace(
            '{{ tpl (toYaml .) $ }}', '{{ unsupported }}'), values)
