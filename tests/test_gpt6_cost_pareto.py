import json
from math import isclose
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.build_gpt6_cost_pareto import Point, load_points, pareto_frontier, render_svg


def test_pareto_frontier_requires_no_cheaper_higher_scoring_point() -> None:
    points = [
        Point('sol', 'low', 100_000, 1.0, 4000, 1),
        Point('sol', 'medium', 200_000, 2.0, 4500, 1),
        Point('sol', 'high', 300_000, 3.0, 4200, 1),
        Point('astra', 'low', 80_000, 4.0, 5000, 1),
        Point('astra', 'high', 100_000, 5.0, 5000, 1),
    ]
    assert pareto_frontier(points) == [points[0], points[1], points[3]]


def test_chart_uses_leaderboard_output_tokens_and_official_output_rates(tmp_path) -> None:
    models = []
    for family in ('astra', 'sol'):
        for effort in ('low', 'medium', 'high', 'xhigh', 'max'):
            models.append({
                'model': f'gpt-6-{family}-{effort}', 'runs': 1,
                'efficiency': {'output_tokens': {'mean': 100_000}},
                'rank_score': {'mean': 5000.0},
            })
    path = tmp_path / 'leaderboard_data.json'
    path.write_text(json.dumps({'boards': [{'preset': 'full', 'version': 'v1', 'models': models}]}), encoding='utf-8')
    points = load_points(path, preset='full', version='v1')
    assert {p.cost for p in points if p.family == 'astra'} == {5.0}
    assert {p.cost for p in points if p.family == 'sol'} == {1.0}
    svg = render_svg(points)
    assert svg.count('<g aria-label="gpt-6-') == 10
    assert 'https://developers.openai.com/api/docs/pricing' in svg
    root = ET.fromstring(svg)
    groups = root.findall('.//{http://www.w3.org/2000/svg}g[@data-model]')
    assert {g.attrib['data-model'] for g in groups} == {p.name for p in points}
    assert {g.attrib['data-model'] for g in groups if g.attrib['data-frontier'] == 'true'} == {
        p.name for p in points if p.family == 'sol'
    }
    assert '每档仅 1 次运行' in ''.join(root.itertext())


def test_chart_log_scale_keeps_cost_ratios_and_scores() -> None:
    points = [
        Point('sol', 'low', 100_000, 1.0, 4000, 1),
        Point('sol', 'medium', 200_000, 2.0, 5000, 1),
        Point('sol', 'high', 400_000, 4.0, 6000, 1),
    ]
    root = ET.fromstring(render_svg(points))
    groups = root.findall('.//{http://www.w3.org/2000/svg}g[@data-model]')
    xs = [float(g.attrib['data-x']) for g in groups]
    ys = [float(g.attrib['data-y']) for g in groups]
    assert isclose(xs[1] - xs[0], xs[2] - xs[1], abs_tol=0.02)
    assert isclose(ys[0] - ys[1], ys[1] - ys[2], abs_tol=0.02)
    assert [float(g.attrib['data-cost']) for g in groups] == [p.cost for p in points]
    assert [float(g.attrib['data-score']) for g in groups] == [p.score for p in points]
    assert '对数刻度' in ''.join(root.itertext())
