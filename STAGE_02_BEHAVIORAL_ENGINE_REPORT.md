# تقرير محرك المؤشرات السلوكية — Stage 2

**تاريخ التنفيذ:** 21 أكتوبر 2026  
**جاهزية محرك المؤشرات السلوكية العملية:** PASS  
**جاهزية تنبؤ AI معتمد للتعب المهني:** BLOCKED  
**جاهزية AI للتنبؤ باليقظة:** BLOCKED

## الخلاصة

أُضيف محرك وصفي بإصدار `wellar.behavioral-evidence/1.0` داخل مسار الـscan الحالي، ويظهر في نتيجة الـAPI كحقل إضافي `behavioral_evidence`. ينتج مخرجين مستقلين: `visible_fatigue_indicators` و`vigilance_indicators`. يمرر المحرك مشاهدات وقياسات موجودة فعلاً مع الجودة والتغطية ومبررات الامتناع؛ ولا يحسب درجة تعب أو تركيز، ولا ينتج نسبة أو ثقة معايرة أو تشخيصاً. التنبؤات البحثية تظل معطلة، والتقرير الذاتي الاختياري يظل في `measurements.fatigue` منفصلاً عن المخرجين.

## الملفات التي أُضيفت أو عُدلت

- `app/behavioral/engine.py` و`app/behavioral/__init__.py`: العقد الإصدارّي، التحقق من إصدار وحقول FeatureEnvelope وأنواعها ومداها، حالات الأدلة والجودة والامتناع، وربط ملاحظات الرؤية ومهمة الاستجابة.
- `app/media/extractors.py`: استخراج وسيطي EAR وMAR من قياسات الإطارات الموجودة بالفعل؛ لا تُعاد مصفوفات الإطارات الكبيرة في نتيجة الـAPI، وتُحجب القيم عند فشل استمرارية المسار.
- `app/media/analysis.py`: استدعاء المحرك على مخرجات الاستخراج وقياسات المهمة الموجودة دون فك وسائط إضافي.
- `app/media/contracts.py` و`app/scans/schemas.py`: إضافة حقل النتيجة دون حذف الحقول القديمة.
- `app/workers/worker.py`: شكل آمن ثابت يعيد `not_provided` عند غياب التحليل الاختياري.
- `tests/test_behavioral_engine.py`: اختبارات العقد، الجودة والامتناع، وscan متعدد الوسائط عبر API وworker بوسائط اصطناعية.
- `tests/test_drowsiness_indicator_api.py`: تصحيح توقع قديم متعارض مع عقد Stage 1 الحالي؛ الاختبار الآن يثبت `not_performed` وعدم وجود label أو state أو level أو confidence أو accuracy، مع استمرار التحقق من خصوصية KSS.
- `benchmarks/behavioral_feature_payload_v1.json` و`scripts/benchmark_behavioral_engine.py`: حمولة ثابتة وأداة قياس محدودة قابلة لإعادة التشغيل.

## المخرجات والقيود

### مؤشرات التعب المرئية

المعنى: «سلوكيات مرئية قد تصاحب التعب أحياناً، لكنها لا تحدد مستوى تعب الشخص». عند توافر جودة مناسبة قد تتضمن وجود/عدد الوجه، EAR وMAR الهندسيين، مؤشرات الإضاءة والحدة، وعينات الوجه وتغطية الإطارات ومدة الفيديو. يضيف Stage 1 وسيطي EAR وMAR للفيديو من القياسات التي استخرجها فعلاً.

نسبة إغلاق العين القصير لا تظهر إلا إذا أعلن Stage 1 أنها صالحة وكانت استمرارية المسار المكانية متحققة. أحداث الرمش ومدة الإغلاق تتطلب معدل أخذ عينات لا يقل عن 10 Hz. أحداث/مدة/نسبة فتح الفم تتطلب 5 Hz على الأقل؛ المعدل الافتراضي 4 Hz، لذلك تُحجب هذه القياسات في الحالة المعتادة. لا يُسمى فتح الفم تثاؤباً. لا تُستنتج حركة الرأس أو nodding من اتجاه منفرد؛ قيم yaw/pitch المتاحة مؤشرات اتجاه نسبية فقط. لا يُسمى إغلاق العين القصير PERCLOS سريرياً. حجب الكاميرا `not_assessed`.

الوجه المفقود، landmarks غير الصالحة، الإضاءة/الحدة الضعيفة أو تعذر تتبع وجه واحد ينتج أدلة جزئية أو غير كافية مع سبب؛ ولا يُفسّر الغياب على أنه سلوك طبيعي.

### مؤشرات اليقظة

المعنى: «أداء الاستجابة الملحوظ أثناء مهمة قصيرة، مع عرض الملاحظات البصرية بشكل منفصل؛ لا يثبت ذلك التركيز العام أو صلاحية العمل». تُمرر أزمنة الاستجابة الصحيحة بالمللي ثانية، الوسيط وP90، أعداد التجارب الصحيحة/المقدمة والبدايات المبكرة والمتأخرة والفائتة والملتبسة، نسبة التجارب الصالحة، حالة إكمال المهمة، ودقة الساعة/الادعاء الزمني من العميل. يُتحقق من اتساق المتجه والملخص والبروتوكول؛ وتُرفض التناقضات.

توقيت العميل يظل `unverified_client_clock` و`server_verified=false`. لا تُسمى المهمة PVT سريرية، ولا تتحول ثلاث تجارب إلى درجة قدرة يقظة. ملاحظات العين والسياق الصوتي منفصلة عن أزمنة الاستجابة ولا تُدمج في درجة.

### الصوت، التقرير الذاتي، والتنبؤ

الصوت يساهم في وجود الإشارة وجودتها ومؤشرات المشاركة/النشاط الصوتي المتاحة بوصفها heuristic غير معتمدة فقط. لا يستنتج المحرك التعب أو التركيز أو الانفعال من مستوى الصوت أو الطيف أو النشاط الشبيه بالكلام. التقرير الذاتي الاختياري يظل قياساً مباشراً مستقلاً. لا يوجد خرج تنبؤ تشغيلي؛ `model_prediction` يعرض التعطيل صراحة، ولا توجد دقة علمية مثبتة.

## مثال عقد نتيجة الـAPI

هذا مقتطف مبسط يوضح أسماء الحقول، وليس استنتاجاً من شخص:

```json
{
  "behavioral_evidence": {
    "schema_version": "wellar.behavioral-evidence/1.0",
    "visible_fatigue_indicators": {
      "status": "partial_evidence",
      "measurement_type": "visible_fatigue_behavioral_indicators",
      "observations": {"video_median_ear": 0.24, "blink_event_count": null},
      "source_modalities": ["video"],
      "abstention_reasons": ["temporal_sampling_too_sparse_for_blink_events"],
      "inference_kind": "descriptive_observation_no_prediction",
      "validation_status": "not_validated_as_occupational_fatigue"
    },
    "vigilance_indicators": {
      "status": "observed",
      "measurement_type": "brief_reaction_task_and_separate_visual_observations",
      "observations": {"valid_reaction_times_ms": [310, 355, 402], "median_reaction_time_ms": 355},
      "inference_kind": "direct_task_observation_no_prediction",
      "validation_status": "task_observation_only_not_clinical_pvt"
    },
    "model_prediction": {"status": "disabled", "inference_status": "not_performed"}
  }
}
```

القيم في المثال توضيحية وليست نتائج اختبار أو بيانات مستخدم.

## الاختبارات والنتائج

جميع اختبارات الوسائط الاصطناعية تثبت صحة التنفيذ والعقد فقط، ولا تمثل تحققاً علمياً على أشخاص.

| الأمر | النتيجة الفعلية |
|---|---|
| `rtk proxy .\\.venv\\Scripts\\python.exe -m pytest tests\\test_behavioral_engine.py -q` | 14 passed، تحذيران deprecation، 6.50 ثانية، exit 0 |
| `rtk proxy .\\.venv\\Scripts\\python.exe -m pytest tests\\test_behavioral_engine.py::test_complete_synthetic_multimodal_scan_returns_independent_observed_outputs -q` | 1 passed، تحذيران deprecation، 4.50 ثانية، exit 0؛ scan API واحد بمدخلات صورة/فيديو/صوت ومهمة استجابة اصطناعية |
| `rtk proxy .\\.venv\\Scripts\\python.exe -m pytest tests\\test_behavioral_engine.py tests\\test_dual_measurements.py tests\\test_drowsiness_indicator_api.py tests\\test_ai_engine_foundation.py -q` | 46 passed، تحذيران deprecation، 5.32 ثانية، exit 0 |
| `rtk proxy .\\.venv\\Scripts\\python.exe -m pytest tests\\test_behavioral_engine.py tests\\test_dual_measurements.py tests\\test_media.py tests\\test_api.py tests\\test_drowsiness_indicator_api.py -q` | 65 passed، تحذيران deprecation، 42.29 ثانية، exit 0 |
| حزمة media/worker/API/release-regressions المبينة أدناه | 183 passed، تحذيران deprecation، 47.69 ثانية، exit 0 |
| `rtk proxy .\\.venv\\Scripts\\python.exe scripts\\benchmark_behavioral_engine.py` | exit 0؛ تفاصيل الأداء أدناه |

حزمة الاختبارات الواسعة:

```text
rtk proxy .\\.venv\\Scripts\\python.exe -m pytest tests\\test_behavioral_engine.py tests\\test_dual_measurements.py tests\\test_audio_vad.py tests\\test_video_continuity.py tests\\test_synthetic_face_quality.py tests\\test_media.py tests\\test_mp4_media.py tests\\test_real_media_acceptance.py tests\\test_isolated_decode.py tests\\test_media_feature_adapter.py tests\\test_worker_inference_integration.py tests\\test_research_drowsiness_worker.py tests\\test_worker_startup.py tests\\test_api.py tests\\test_media_reconciliation.py tests\\test_hardening_media_cleanup.py tests\\test_ai_engine_foundation.py tests\\test_drowsiness_indicator.py tests\\test_drowsiness_indicator_api.py -q
```

في إعادة التشغيل النهائية للحزمة الأوسع: 183 passed، تحذيران deprecation، 47.69 ثانية، exit 0. ظهر في تشغيل سابق فشل لتوقع API قديم؛ بعد مطابقته مع عقد Stage 1 واختبار regressions، عُدّل التوقع لا سلوك الإنتاج، ونجح التشغيل المعزول 46/46 ثم الحزمة الأوسع 183/183. لم يُخفَ الفشل الأول.

## قياس زمن المحرك

الأمر: `rtk proxy .\\.venv\\Scripts\\python.exe scripts\\benchmark_behavioral_engine.py`، exit 0. على حمولة ثابتة 4,785 بايت (SHA-256: `df664fe69bcd6c71c725e8c66c6e137dc892d9fa784197bcb721b0f8e25c07ff`)، مع 20 warm-up و500 تكرار:

| القياس | النتيجة |
|---|---:|
| median | 0.0871 ms |
| P95 | 0.1476 ms |
| maximum | 0.2365 ms |
| CPU time / 500 تكرار | 0.046875 s |
| ذروة `tracemalloc` خلال 20 إسقاطاً إضافياً | 7,446 bytes |
| حجم المخرجات المتسلسلة | 8,995 bytes |
| فرق حجم الحمولة/المخرج | 4,210 bytes |

بيئة القياس: Python 3.10.11، Windows 10 build 26100، Intel64 Family 6 Model 158 Stepping 9، و8 معالجات منطقية. القياس يخص إسقاط features في الذاكرة فقط؛ لا يشمل فك الوسائط أو API أو التخزين أو inference، ولا يثبت هدف معالجة scan كاملة خلال 3–5 ثوانٍ أو هدف 2,000 scan.

## الخصوصية والموافقة

المسار يستخدم مخزن الوسائط والـworker والـAPI وقيود الوصول الحالية، ولا ينشئ مسار scan آخر أو فكاً إضافياً. الاختبار المتكامل اصطناعي، واختبار tenant isolation ضمن regressions. لم تُستخدم وسائط أشخاص أو بيانات مشاركين. لا يضيف المحرك تعرّف هوية أو transcription أو speaker identification أو تحليل مشاعر. تظل موافقة الوسائط الحالية مطلوبة في مسار الالتقاط.

## الحكم والخطوة التالية

**محرك المؤشرات السلوكية العملية: PASS** — كائنات النتيجة additive وموجودة في scan API، المخرجات مستقلة، القياسات مقيدة ببيانات Stage 1 الفعلية، والامتناع صريح عند الجودة/التغطية غير الكافية.  
**AI تنبؤي معتمد للتعب المهني: BLOCKED** — لا توجد بيانات تحقق مستقلة أو نموذج معتمد لهذا الهدف.  
**AI تنبؤي لليقظة: BLOCKED** — ملاحظة مهمة الاستجابة لا تثبت صلاحية تنبؤ أو PVT معتمد.

الخطوة التالية هي عرض الملاحظات تحت هذه التسميات الحذرة وبمفاتيح تفسير قابلة للترجمة، ثم جمع دليل تحقق منفصل قبل أي ادعاء تنبؤي. لا يبدأ هذا التقرير Stage 3.
