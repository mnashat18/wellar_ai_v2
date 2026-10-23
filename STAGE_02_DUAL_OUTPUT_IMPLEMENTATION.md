# Wellar AI V2 — Stage 2: إغلاق القياسات العملية

**التاريخ:** 2026-10-21  
**حالة القياس المباشر:** جاهز على مستوى backend لعقد القياس المتاح  
**حالة AI التنبؤي:** غير متحقق علميًا، ومعطّل افتراضيًا  
**حكم Stage 2 الكلي:** **PARTIAL** — القياسات العملية منفصلة ومتاحة، لكن Flutter الحالي لم يثبت أو يرسل بروتوكول السؤال الجديد، ولا توجد بيانات مستقلة تحقق تنبؤ AI.

## ملخص التنفيذ

النظام بيرجع مخرجين منفصلين داخل `measurements` تحت مخطط `wellar.dual-measurements/1.0`: `fatigue` و`vigilance`. كل مخرج يقدر يبقى متاح أو غير متاح مستقلاً. مفيش دمج بينهم في درجة واحدة، ومفيش استنتاج تعب من الصور أو الفيديو أو الصوت أو زمن الاستجابة.

## قياس التعب المبلغ عنه ذاتيًا

اتضاف مسار قياس مباشر، مش مقياسًا مهنيًا معتمدًا ولا prediction. السؤال الأصلي المحدد في `wellar-fatigue-self-report/1.0` هو:

> Right now, how physically or mentally fatigued do you feel before starting work?

- الفترة: «دلوقتي، قبل بداية العمل».
- التدريج: قيمة صحيحة من 0 إلى 10؛ 0 = «Not fatigued at all»، و10 = «Extremely fatigued».
- يتطلب ردًا صريحًا وموافقة `collection_consent=true`. القيمة من غير الموافقة تُمسح قبل التخزين؛ الموافقة مع إجابة غائبة أو قيمة خارج النطاق مرفوضة عند schema boundary.
- الـAPI يعرض `measurement_type=self_reported_fatigue`، القيمة والوحدة `custom_0_to_10_scale`، المصدر `participant_entered`، نسخة السؤال، نص السؤال، الفترة والـanchors. حالته `observed_self_report` و`ai_validation_status=not_validated`.
- البحث للقراءة فقط في `D:\flutter\waller_app\lib` لم يجد ربطًا بين مدخل 0–10 وبين نص السؤال أو الفترة أو anchors أو حقل `subjective_fatigue`. لذلك الـpayload القديم يظل مقبولًا للتوافق لكنه يظهر `observed_legacy_self_report_protocol_unverified` ولا تُنسب له صياغة أو فترة غير مثبتة. العميل لازم يرسل `question_version=wellar-fatigue-self-report/1.0` فقط لو عرض السؤال المطابق بالفعل. لم يتغير Flutter حسب حدود المرحلة.
- المقياس الأصلي غير متحقق الصلاحية، ولا يمثل تقييمًا طبيًا أو قرار جاهزية/ملاءمة للعمل. موافقة المعالجة لا تعني موافقة التدريب أو البحث؛ `training_consent=false` يظل قائمًا.

لو مفيش رد وموافقة، يظهر التعب `unavailable` بقيمة `null`. مفيش ملء تلقائي أو fallback من أي modality.

## قياس اليقظة بالمهمة القصيرة

الـbackend يحسب فروق الطوابع الزمنية التي يرسلها العميل، ويعرض القائمة `valid_reaction_times_ms`، والوسيط وP90، وعدد المحاولات الصحيحة، والمتأخرات، والبدايات المبكرة، والاستجابات المفقودة، وتعارض إسناد الاستجابة بين المحاولات، ونسبة المحاولات الصحيحة، وحالة الإكمال التي أبلغ عنها العميل.

- حدود التنفيذ الحالية: أقل من 100ms = بداية مبكرة/كاذبة؛ من 100 إلى 2000ms ضمن العينة؛ أكبر من 2000ms استجابة متأخرة. دي حدود معالجة تقنية للمهمة وليست حدودًا علمية معتمدة.
- الاستجابة التي تقع بعد stimulus المحاولة التالية تُصنف `timer_or_trial_attribution_anomaly` وتُستبعد من أزمنة الاستجابة الصحيحة. أحداث العميل محفوظة مع حالة ساعة غير متحققة؛ ادعاء monotonic أو الدقة المرسلة لا يصبح تحققًا من السيرفر.
- عند عدم وجود 3 محاولات صالحة على الأقل، الجودة `insufficient_valid_trials`. وجود 3 أو أكثر يبقى `unverified_client_timing`، وليس نتيجة PVT معتمدة.
- إصلاح إسقاط `valid_reaction_times_ms`: كانت موجودة في المخرج التفصيلي وتسقط من `features.reaction_task` بسبب مرشح scalar-only. دلوقتي القائمة الصحيحة تظهر في العقدين، مع إبقاء كل trial observations التفصيلية في القياس ذي النسخة.
- ده رصد لأداء مهمة قصيرة بتوقيت عميل، مش درجة يقظة معايرة، ولا PVT مكافئ سريريًا، ولا استنتاج تعب أو سلامة.

## فصل الذكاء الاصطناعي

مدخل `app/ml/dual_feature_adapter.py` يظل adapter مستقبليًا ذا نسخة `wellar.dual-predictor-input/1.0`، ولا يدرب أو يشغل model. predictor invocation ودمج النماذج يظلان معطلين افتراضيًا وبحاجز الإصدار العلمي الحالي. الـAPI يحافظ على targets التنبؤية غير المتاحة، ومخرجات direct measurement لا تتحول إلى model predictions.

- **قياس مباشر مُلاحظ:** متاح للتقرير الذاتي المصرح به ولملاحظات التوقيت، مع حدود المصدر والجودة أعلاه.
- **تقدير AI بحثي:** لا يوجد هنا artifact inference مفعل.
- **تقدير AI متحقق:** غير متاح. لا توجد دراسة مستقلة للهدف المقصود مع تحقق participant-disjoint ومعايير قبول/معايرة متفق عليها. نتائج KSS/PERCLOS البحثية السابقة تخص هدفًا ومجالًا مختلفين ولا تثبت تعب العمل أو اليقظة في هذا المنتج.

## التغييرات في هذه الإغلاق

- `app/scans/schemas.py`: نسخة السؤال، حدود القيمة، اشتراط الرد بعد الموافقة، وإزالة القيمة عند رفض الموافقة.
- `app/measurements/dual_output.py`: مخرج self-report المباشر الواضح، مع protocol legacy غير موثق منفصلًا، والحفاظ على القياس الانعكاسي غير المعتمد مغلقًا.
- `app/media/analysis.py`: تمرير نسخة السؤال إلى التوثيق، والاحتفاظ بقائمة أزمنة RT الصحيحة في feature envelope.
- `app/media/contracts.py`: السماح بالقائمة الصحيحة المحددة داخل feature envelope دون توسيع عام للأنواع المتداخلة.
- `app/workers/worker.py`: تمرير self-report المصرح به عبر safe default processor دون تفعيل تحليل الوسائط أو predictor.
- `tests/test_dual_measurements.py`, `tests/test_media.py`, `tests/test_api.py`: تغطية عقد القياسين والتكامل/التسلسل والحالات القديمة والجديدة.
- `STAGE_02_DUAL_OUTPUT_IMPLEMENTATION.md`: هذا التقرير.

## دليل الاختبارات

الاختبارات البرمجية تستخدم بيانات/طوابع زمنية اصطناعية deterministic؛ نجاحها يثبت السلوك البرمجي فقط، وليس صحة علمية أو معايرة ساعة الجهاز أو تحقق السؤال الذي عرضه تطبيق فعلي.

### تشغيل مركز

```text
rtk proxy .\.venv\Scripts\python.exe -m pytest -q tests/test_dual_measurements.py tests/test_media.py tests/test_api.py tests/test_worker_startup.py tests/test_worker_inference_integration.py tests/test_real_media_acceptance.py tests/test_mp4_media.py tests/test_synthetic_face_quality.py tests/test_video_continuity.py tests/test_audio_vad.py
```

النتيجة النهائية بعد آخر تعديل schema: **Exit 0 — 88 passed، تحذيران deprecation من Starlette/AnyIO، خلال 29.11 ثانية**.

أثناء التطوير، تشغيل أول انتهى **Exit 1 — 55 passed، 2 failed** بسبب توقع الاختبار لحالة قديمة `not_activated`، وبسبب assertion أُضيفت في test غير مناسب. التشغيل التالي انتهى **Exit 1 — 87 passed، 1 failed**؛ تحليل الطوابع أثبت أن استجابة المحاولة الثانية وصلت بعد stimulus المحاولة الثالثة، لذلك تصنيفها anomaly واستبعادها كان صحيحًا. صححت الاختبار ليطلب `[250]` وعدّ anomaly=1. التشغيل النهائي أعلاه نجح.

### تشغيل regression الأوسع

```text
rtk proxy .\.venv\Scripts\python.exe -m pytest -q tests/test_dual_measurements.py tests/test_dual_feature_adapter.py tests/test_audio_vad.py tests/test_video_continuity.py tests/test_synthetic_face_quality.py tests/test_media.py tests/test_mp4_media.py tests/test_real_media_acceptance.py tests/test_isolated_decode.py tests/test_media_feature_adapter.py tests/test_worker_inference_integration.py tests/test_research_drowsiness_worker.py tests/test_worker_startup.py tests/test_api.py tests/test_media_reconciliation.py tests/test_hardening_media_cleanup.py tests/test_ai_engine_foundation.py tests/test_directus_pull.py tests/test_dataset_qualification.py tests/test_research_evaluation.py tests/test_research_regression_training.py tests/test_research_model_input.py tests/test_ml_training_pipeline.py tests/test_short_mobile_kss.py tests/test_kss_perclos_paired_statistics.py tests/test_research_image_drowsiness.py tests/test_research_manifest_validation.py tests/test_owned_pilot_workflow.py
```

النتيجة النهائية بعد آخر تعديل schema: **Exit 0 — 273 passed، تحذيران deprecation من Starlette/AnyIO، خلال 57.14 ثانية**. تشمل الاختبارات API وworker startup/inference، إخراج القياس، استخراج image/video/audio الاصطناعي، MP4، جودة/استمرارية الوجه، decoding المعزول، reconciliation، والاختبارات البحثية/التدريبية كـsoftware regression فقط. لا تعد الاختبارات البحثية تحققًا علميًا جديدًا.

فحوص إضافية: `rtk proxy .\\.venv\\Scripts\\python.exe -m compileall -q app/measurements app/scans/schemas.py app/media/analysis.py app/media/contracts.py app/workers/worker.py` — **Exit 0**؛ `rtk git diff --check` — **Exit 0**.

## حدود متبقية وحكم المرحلة

1. Flutter الحالي لم يثبت أو يرسل نسخة السؤال الجديدة حسب البحث read-only. الـbackend يقبل القيمة القديمة كـself-report مع provenance غير مكتمل؛ عرضها للمستخدم/الموظف لازم يوضح أنها غير متحقق من صياغتها.
2. المهمة القصيرة لا تملك تحققًا من ساعة الجهاز ولا تكافؤًا مثبتًا مع PVT علمي.
3. لا توجد صلاحية علمية للمقياس الأصلي ذي السؤال الواحد ولا AI متحقق لهدف التعب أو اليقظة.
4. صور وفيديو وصوت Stage 1 يظلون إشارات وصفية فقط ولا يشتق منهم التعب أو نتيجة اليقظة.

**جاهزية القياس العملي:** متاحة في backend كـself-report صريح ومشروط بالموافقة، ورصد reaction-task منفصل وغير معاير. **التنبؤ العلمي:** غير متاح ومعطل.  
**حكم Stage 2: PARTIAL** — تم إغلاق عقد القياسات العملية في backend؛ إغلاق المرحلة بالكامل يظل معطلًا بسبب عدم ربط واجهة العميل بالبروتوكول الجديد، وغياب دليل تحقق AI مستقل. لا يبدأ Stage 3 هنا.
