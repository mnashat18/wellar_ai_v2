# Wellar V2 P0 local CPU performance evidence

## Scope

One permitted local functional run used only media referenced by the session manifest under the consent authorization for local media analysis. The run did not print media, participant IDs, predicted labels, or individual measured feature values. It did not train or evaluate a model and does not estimate service capacity.

Runtime: isolated Python environment `wellar-doze-runtime-p0-20261016`; PyTorch 2.6.0 CPU, torchvision 0.21.0 CPU, timm 1.0.15, one intra-op CPU thread. Host was Windows, 8 logical CPUs; no GPU was used. Peak process RSS is measured for the process, not incremental model memory.

## Selected MobileViT model

- Cold architecture + weights load: **10,530.151 ms** in the full worker smoke run.
- Worker-run image classifier invocation: **348.728 ms** (one permitted image, one observation; not a percentile).
- Separate five-call warm benchmark median: **303.527 ms**; values 298.742, 327.857, 300.483, 303.527, 329.043 ms. The operational harness's five warm calls had median **387.993 ms**; values 385.338, 361.816, 387.993, 398.469, 427.685 ms. These are two different benchmark harnesses, not pooled.
- Full worker-harness processing after model initialization: **3,806.671 ms**.
- Process peak RSS: **580,067,328 bytes**; RSS after model load: **421,646,336 bytes**; before load: **45,953,024 bytes**.
- Process CPU time including initialization + scan: **15.312 seconds**. CPU time is not elapsed wall time.

## Same worker run modality stages

- Image feature path total: 2,233.320 ms (includes image decode and existing face/landmark extraction).
- Video decoded 141 frames, sampled 18; decode 725.593 ms, total extraction 1,082.192 ms.
- WAV quality/features: 2.646 ms.
- All three modality extraction statuses were available in this one local functional run. That indicates successful software processing only, not prediction or representativeness.
- No fatigue/sleepiness/vigilance target prediction was available. Research classifier invocation status was `research_only`.

## Earlier large-model comparison

DOZE-GUARD loaded and executed in the worker harness before selection was changed: cold load 15,490 ms, warm median 1,052.163 ms over five calls, peak RSS 820,551,680 bytes, worker processing 4,337.647 ms after model load. Its size/startup/rights/evidence profile was inferior for this non-production comparison. Do not compare the single operational call latencies as statistically robust.

## Interpretation

This desktop CPU did not meet a seven-second scan objective once cold initialization is included, and this is not a deployment-capacity benchmark. Warm worker time was a single run on one process, without concurrent load or separate upload/queue measurements. No concurrency test was conducted. The local machine's specifications cannot justify production worker/GPU capacity or 2,000 concurrent scans.
