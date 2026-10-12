"""``adgate label|study|feedback`` 명령. 메인 CLI 가 이 모듈의 ``register`` 를 부른다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..eval import dataset
from . import feedback, queue, study
from .adjudicate import simulate as simulate_adjudication

DATA = Path(__file__).resolve().parents[3] / "data"


def _load(names: str) -> list[dataset.Example]:
    return [e for n in names.split(",") if n for e in dataset.load(DATA / f"{n}.jsonl")]


def cmd_label_queue(args: argparse.Namespace) -> int:
    from ..judge_recorded import RecordedJudge

    items = _load(args.datasets)
    judge = RecordedJudge.from_file(DATA / "judge_live" / "recording.jsonl") if args.variant == "engine+cues+judge" else None
    ranked = queue.rank(items, args.variant, judge)
    for r in ranked[: args.top]:
        print(f"{r.score:5.1f}  {r.id:10s} 엔진 {r.outcome:8s} {' · '.join(r.reasons) or '-'}")
    return 0


def cmd_label_simulate(args: argparse.Namespace) -> int:
    res = {f"a{round(a * 100)}": simulate_adjudication(args.n, a, seed=args.seed) for a in (0.8, 0.9, 0.95)}
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


def _items(args: argparse.Namespace) -> list[study.StudyItem]:
    return study.select_items(_load(args.datasets), per_stratum=args.per_stratum, seed=args.seed)


def cmd_study_kit(args: argparse.Namespace) -> int:
    out = Path(args.out)
    (out / "booklets").mkdir(parents=True, exist_ok=True)
    (out / "INTERNAL").mkdir(parents=True, exist_ok=True)
    items = _items(args)
    trials = study.design(items, args.reviewers, seed=args.seed)
    by = {i.id: i for i in items}
    dev = dataset.load(DATA / "dev.jsonl")
    practice = study.practice_items(dev)
    did = study.design_id(items, trials, args.seed)
    for r in sorted({t.reviewer for t in trials}):
        (out / "booklets" / f"{r}.html").write_text(study.booklet(r, trials, by, practice, did), encoding="utf-8")
    (out / "INTERNAL" / "answer_key.json").write_text(json.dumps({"design_id": did, "items": [i.__dict__ for i in items], "trials": [t.__dict__ for t in trials]}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "README.txt").write_text(
        "booklets/ 아래 R01.html … 을 검토자 한 명에게 하나씩 준다(브라우저로 열면 된다, 인터넷 불필요).\n"
        "검토자가 끝나면 받는 R0N.json 을 responses/ 에 모은다. INTERNAL/ 은 연구 담당자만 보관한다(정답이 들어 있다).\n"
        "분석: adgate study analyze --key INTERNAL/answer_key.json --responses responses/*.json\n", encoding="utf-8",
    )
    print(f"책자 {args.reviewers}개 → {out}/booklets  (설계 {did}, 소재 {len(items)}건, 검토자당 {len(trials) // args.reviewers}건)")
    return 0


def cmd_study_analyze(args: argparse.Namespace) -> int:
    key = json.loads(Path(args.key).read_text(encoding="utf-8"))
    items = {i["id"]: study.StudyItem(**{**i, "engine_rules": tuple(i["engine_rules"]), "engine_quotes": tuple(tuple(q) for q in i["engine_quotes"])}) for i in key["items"]}
    resp: list[study.Response] = []
    for f in args.responses:
        resp += study.parse_responses(json.loads(Path(f).read_text(encoding="utf-8")))
    res: dict[str, Any] = study.analyze(resp, items, seed=args.seed)
    text = json.dumps(res, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


def cmd_study_simulate(args: argparse.Namespace) -> int:
    items = _items(args)
    params = study.SimParams(help_when_engine_right=args.help, follow_when_engine_wrong=args.follow, speedup=args.speedup)
    res = study.power(items, params, reviewers=[int(x) for x in args.reviewers.split(",")], sims=args.sims, seed=args.seed)
    text = json.dumps(res, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


def cmd_feedback_mine(args: argparse.Namespace) -> int:
    from ..store import Store

    store = Store(args.db)
    items = feedback.mine(store)
    stats = feedback.override_stats(items)
    if args.out:
        n = feedback.export_candidates(items, Path(args.out))
        stats["exported"] = n
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0


def register(sub: Any) -> None:
    s = sub.add_parser("label", help="라벨링 우선순위 큐 · 이중 라벨 시뮬레이션")
    ls = s.add_subparsers(dest="label_cmd", required=True)
    q = ls.add_parser("queue", help="우선순위 큐(정답 없이 점수를 매긴다)")
    q.add_argument("--datasets", default="redteam,gray")
    q.add_argument("--variant", choices=list(queue.VARIANTS), default="engine+cues")
    q.add_argument("--top", type=int, default=30)
    q.set_defaults(fn=cmd_label_queue)
    m = ls.add_parser("simulate", help="이중 라벨 + 중재 시뮬레이션")
    m.add_argument("--n", type=int, default=2000)
    m.add_argument("--seed", type=int, default=0)
    m.set_defaults(fn=cmd_label_simulate)

    st = sub.add_parser("study", help="사람 실험 키트: kit · analyze · simulate")
    ss = st.add_subparsers(dest="study_cmd", required=True)
    for name, fn, helptext in (("kit", cmd_study_kit, "책자·배정·정답 키 생성"), ("simulate", cmd_study_simulate, "시뮬레이션 참가자로 검출력 보기")):
        p = ss.add_parser(name, help=helptext)
        p.add_argument("--datasets", default="redteam,gray")
        p.add_argument("--per-stratum", type=int, default=16)
        p.add_argument("--seed", type=int, default=1)
        if name == "kit":
            p.add_argument("--reviewers", type=int, default=8)
            p.add_argument("--out", required=True)
        else:
            p.add_argument("--reviewers", default="8,12,16,24")
            p.add_argument("--sims", type=int, default=200)
            p.add_argument("--help-effect", dest="help", type=float, default=0.05)
            p.add_argument("--follow", type=float, default=0.3)
            p.add_argument("--speedup", type=float, default=0.8)
            p.add_argument("--out", default="")
        p.set_defaults(fn=fn)
    a = ss.add_parser("analyze", help="응답 파일 분석")
    a.add_argument("--key", required=True)
    a.add_argument("--responses", nargs="+", required=True)
    a.add_argument("--seed", type=int, default=0)
    a.add_argument("--out", default="")
    a.set_defaults(fn=cmd_study_analyze)

    f = sub.add_parser("feedback", help="운영 사람 검토 결과를 오탐·미탐 후보로 되돌린다(환류)")
    fs = f.add_subparsers(dest="feedback_cmd", required=True)
    mm = fs.add_parser("mine", help="Store 에서 사람이 결정한 건을 모아 재정의율과 후보를 낸다")
    mm.add_argument("--db", required=True)
    mm.add_argument("--out", default="", help="오탐·미탐 후보 JSONL")
    mm.set_defaults(fn=cmd_feedback_mine)
