# 能力飞轮 —— 磁盘数据契约(loop engineering 的 I/O 层)

这份文件定义循环**输出哪些文件、字段是什么、谁读谁、错误怎么解析**。没有这层,
ESPRESSO_CAPABILITY_FLYWHEEL.md 只是意图;有了这层才是可执行的循环。

所有路径相对仓库根。一次"campaign"= 外层飞轮的一次完整运行(N 圈)。

```
benchmarks/
  capability_v0.yaml            # 输入:命题库(下方契约 A)
  split_v0.yaml                 # 输入:dev/test 分裂 + test 集 sha256(冻结)
eval_runs/<campaign_id>/
  cycle_000_baseline/
    runs/<prop_id>/             # espresso 每题产的 run_dir(已有结构,契约 C)
    scorecard.json              # 输出:本圈聚合分(契约 D)
    diagnoses.jsonl             # 输出:每题错误分析,一行一题(契约 E)
  cycle_001/
    change.json                 # 本圈改了机器哪里(契约 F)
    runs/<prop_id>/
    scorecard.json
    diagnoses.jsonl
    replay_decision.json        # 输出:accept/reject + 理由(契约 G)
  gap_report.json               # 输出:跨圈缺口排序(契约 H)
  negative_results.jsonl        # 输出:被否的改动,不重试(契约 I)
  campaign.json                 # 输出:campaign 级状态/停机原因
```

---

## 契约 A — `benchmarks/capability_v0.yaml`(输入)
```yaml
- id: p2_abelian            # str, 唯一
  statement: "..."          # str, 喂给 espresso 的 claim 文本
  known_truth: true|false|open
  oracle: none|exact_arithmetic|finite_enumeration|reference_proof
  oracle_spec: {...}        # 给 falsifier 的检查参数(按 oracle 类型)
  split: dev|test           # 由 split_v0.yaml 决定,这里冗余便于读
```

## 契约 C — `runs/<prop_id>/`(每题 run_dir,**espresso 已产出,循环只读**)
读法:**先读 `run_manifest.yaml` 的 `artifacts` / `optional_artifacts` 映射拿到真实文件名**
(绝不硬编码文件名 —— 和 cli.py inspect 同一纪律),再读下列工件取字段:

| 工件(manifest key) | 读的字段 | 用途 |
|---|---|---|
| `computation_checks` | `checks[].status` (`checked`/`wrong`/`human_needed`)、`checks[].machine_check` | 证伪率(`wrong`)、grounding(`checked`) |
| `source_grounding` | `steps[].status` (`source_backed`/candidate)、`source_refs[]` | grounding 覆盖 |
| `idea_grade` | `status`、`score`、`proof_language_status`、`source_gaps[]` | 校准的预测概率、诚实 |
| `review` | `reviewers[].verdict` (`fail`)、`strongest_objection` | 反对意见 |
| `failure_classification` | `classifications[].category` | 错误归类(契约 E) |
| `prover_loop` (optional) | `loop_decision`、`budget.{rounds_used,max_rounds,agent_calls_used}`、`attempts[].blockers` | 死路/no_progress |
| `locked_claim` | `normalized_statement`、`human_confirmed` | 诚实(human_confirmed 必须 false) |

## 契约 D — `scorecard.json`(输出,每圈一份)
```json
{
  "campaign_id": "...", "cycle": 1, "split": "dev",
  "model": {"prover": "<id+fingerprint>", "judge": "<id>"},
  "n_props": 30, "n_oracle": 12, "repeats_per_prop": 5,
  "metrics": {
    "falsification_rate": {"mean": 0.12, "sd": 0.03, "per_repeat": [..]},
    "grounding_coverage": {"mean": 0.41, "sd": 0.05, "per_repeat": [..]},
    "oracle_accuracy":    {"mean": 0.83, "sd": 0.06, "per_repeat": [..]},
    "calibration_brier":  {"mean": 0.18, "sd": 0.02},
    "calibration_ece":    {"mean": 0.09, "sd": 0.02},
    "honesty_violations": 0,
    "judge_oracle_agreement": 0.91
  },
  "noise_band": {"falsification_rate": 0.06, ...}   // 来自 baseline R 次重复的 2σ
}
```
**每个指标都是 R 次重复的分布(mean+sd+raw),不是单跑**——否则 delta 无法和噪声区分。

## 契约 E — `diagnoses.jsonl`(输出,每题一行,**错误分析的落点**)
```json
{"prop_id":"p2_abelian","outcome":"ok|crash|refuted|objection|no_progress|inconclusive",
 "error_source":"cli_stderr|validate_run|computation_checks|review|prover_loop|none",
 "detail":"<原始报错/objection 文本截断>",
 "refuted_steps":["comp_m2"],
 "failure_categories":["theorem_condition_gap"],     // 来自 failure_classification
 "gap_dimension":"applicability",                    // E→六维 的映射(下表)
 "run_dir":"eval_runs/.../runs/p2_abelian"}
```
错误来源解析顺序(短路):
1. subprocess 返回码≠0 → `outcome=crash`,`error_source=cli_stderr`,解析 stderr 关键字
   (`_validate_human_choice`/effort → `effort_enum_reject`;`KernelValidationError` → `validate_run`)。
2. 否则读 `computation_checks`,有 `status==wrong` → `outcome=refuted`(硬信号)。
3. 否则读 `review`,有 `verdict==fail` → `outcome=objection`,取 `strongest_objection`。
4. 否则读 `prover_loop.loop_decision`/blockers 判 `no_progress`。
5. 都没有 → `outcome=ok`。
6. co-run baseline 漂出带 → `outcome=inconclusive`(整圈作废,不算改善)。

**failure_category → gap_dimension 映射**(契约 H 的频率来源):
```
claim_ambiguous      -> claim_precision
proof_gap            -> strategy_generation
source_gap           -> locator_extraction
theorem_condition_gap-> applicability
computation_unchecked-> verifier_backend
no_progress          -> route_death
(无类别但需外部工具)  -> external_tool
```

## 契约 F — `change.json` / 契约 G — `replay_decision.json` / 契约 H — `gap_report.json` / 契约 I — `negative_results.jsonl`
见 runview.py 的 dataclass。G 的 accept 谓词 = 计划文档"REPLAY 验收谓词"的代码化。
