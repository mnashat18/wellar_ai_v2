# تسليم وقبول Stage 2 العملي — Wellar AI V2

**التاريخ:** 21 أكتوبر 2026  
**الحكم على عقد backend السلوكي:** PASS  
**الحكم الكلي على تسليم Stage 2 العملي:** PARTIAL — مطلوب ربط Flutter لاحقاً  
**Validated occupational fatigue prediction:** NOT VALIDATED  
**Validated vigilance prediction:** NOT VALIDATED

إغلاق هذا النطاق العملي لا يعني اجتياز بوابة نموذج AI العلمية الأصلية. لم يُدرّب نموذج، ولم تُفعّل تنبؤات إنتاجية، ولا توجد دعوى دقة أو صلاحية طبية أو لياقة للعمل.

## دليل التنفيذ ومسار النتيجة

العقد `wellar.behavioral-evidence/1.0` يُنشأ داخل `app/media/analysis.py` من FeatureEnvelopes Stage 1 وقياسات المهمة الحالية. ينتج:

- `behavioral_evidence.visible_fatigue_indicators`
- `behavioral_evidence.vigilance_indicators`
- `behavioral_evidence.model_prediction` بحالة disabled صريحة

الحقل additive في `AnalysisResult` و`ScanResult`، والحقول القديمة باقية. الـworker يمرر النتيجة إلى `store.complete`؛ SQLite يحفظ `result_json` ضمن transaction إكمال المهمة، وPostgreSQL يحفظه في `results.result_json` كـJSONB. بعد الإتمام، `GET /api/v1/scans/{scan_id}/result` يقرأ النتيجة مع قيد tenant وصاحب المسح، ويعيدها عبر response model `ScanResult`. يختبر مسار القبول upload → scan submit → worker extraction → durable result read → GET result فعلياً.

اختبار tenant يؤكد أن صاحب tenant/subject آخر لا يستطيع قراءة النتيجة ويأخذ 404 غير مميز عن المسح غير الموجود. الـworker لا يحذف الوسائط قبل نجاح حفظ النتيجة. لم يظهر `media_ref` أو bytes أو authorization في JSON الناتج. الـJSON يحتوي envelopes والقياسات الوصفية المقصودة للتوافق والعرض، ومنها provenance ومقاييس reaction task؛ هذه بيانات مقاسة وليست صوراً أو صوتاً خاماً. يجب أن تظل قراءتها محكومة بصاحب المسح.

## مخرجات العقد

كل مخرج مستقل، ويحتوي `status`, `measurement_type`, `observations`, `units`, `source_modalities`, `quality`, `coverage`, `evidence`, `limitations`, `method_version`, `inference_kind`, `validation_status`, و`abstention_reasons`. حالات المخرجات تشمل `observed`, `partial_evidence`, `insufficient_evidence`, و`not_provided`؛ لا تُحوّل الأدلة الناقصة إلى حالة طبيعية.

- الرؤية تعرض هندسة الوجه EAR/MAR ومؤشرات الجودة/التغطية عندما تكون صالحة. الاستمرارية المكانية للوجه مطلوبة لتجميع video observations.
- إعداد Stage 1 الافتراضي `video_sample_hz=4.0`. لذلك لا تُعرض أحداث الرمش أو مدة الإغلاق تحت شرط 10 Hz، ولا أحداث/مدد/نسبة فتح الفم تحت 5 Hz. لا يُصنف فتح الفم كتثاؤب، ولا يسمى إغلاق العين القصير PERCLOS. yaw/pitch proxies اتجاهات هندسية فقط؛ لا يوجد head-nodding event detector.
- التوقيتات صحيحة بوصفها client-reported؛ `server_verified=false` و`unverified_client_clock`. لا يُدعى تكافؤ PVT السريري ولا درجة يقظة معايرة.
- audio جودة/سياق مشاركة فقط، وvoice activity heuristic غير معتمد. لا تُستنتج حالة نفسية أو تعب.
- `measurements.fatigue` الاختياري direct self-report مستقل عن الرؤية وعن AI.
- البحث والتنبؤ التشغيلي يظلان معطلين. لا توجد fatigue percentage أو vigilance score أو calibrated confidence.

## مثال نتيجة من اختبار التكامل

المقتطف التالي يطابق حقولاً وقِيَماً تحقّق منها اختبار scan متعدد الوسائط الاصطناعي بعد قراءتها من endpoint التخزين. الصورة الاصطناعية أعطت EAR، والفيديو وصل بجودة غير كافية، ومهمة الاستجابة أعطت الأزمنة المحددة. هذا دليل software فقط وليس تحققاً على أشخاص:

```json
{
  "schema_version": "2.0",
  "behavioral_evidence": {
    "schema_version": "wellar.behavioral-evidence/1.0",
    "visible_fatigue_indicators": {
      "status": "partial_evidence",
      "observations": {
        "eye_aspect_ratio": {"value": 0.24, "units": "ratio", "source": "image"}
      },
      "inference_kind": "descriptive_observation_no_prediction",
      "validation_status": "software_contract_tested_not_scientifically_validated"
    },
    "vigilance_indicators": {
      "status": "observed",
      "observations": {
        "valid_reaction_times_ms": {
          "value": [280, 310, 400], "units": "ms", "source": "client_timestamps"
        },
        "device_timing": {"server_verified": false}
      },
      "inference_kind": "direct_task_observation_no_prediction"
    },
    "model_prediction": {"status": "disabled", "inference_status": "not_performed"}
  },
  "measurements": {
    "fatigue": {"status": "unavailable"},
    "vigilance": {"status": "observed_unverified"}
  },
  "prediction_extension": {"inference_status": "disabled"}
}
```

المقتطف مختصر؛ الاستجابة الكاملة تشمل كذلك quality وcoverage وevidence وlimitations وprovenance وabstention reasons، بالإضافة للحقول القديمة. قيمة EAR الظاهرة قيمة اختبار اصطناعي من extractor وليست معياراً طبيعياً.

## تحقق الخصوصية والتوافق

- الـAPI يشتق tenant/subject من bearer principal؛ لا يثق في tenant header من العميل.
- اختبار cross-tenant result access يثبت رفض القراءة بـ404.
- نتيجة التكامل لا تحتوي مراجع الوسائط التي رفعها الاختبار، ولا بايتات الصور/الفيديو/الصوت أو token.
- تُحفظ المخرجات الوصفية وقياسات reaction المسموح بها؛ فهي لازمة لعقد المخرجات. الحقول العامة `features` تبقى لدعم Stage 1/العملاء الحاليين. لا يوجد مسار يرجع ملفات الوسائط نفسها.
- consent التشغيلي إلزامي عند submit؛ Flutter يرسل `operational-media-v1` و`research_use=false`.
- لم تُعدل Flutter أو Angular أو Directus أو EasyPanel أو production في هذا القبول. لم يُنفذ reset/clean/commit/push. الـworktree كان dirty قبل التنفيذ، وحُفظت تعديلاته الموجودة.

## عقد Flutter الحالي — قراءة فقط

مراجعة `D:\flutter_app\wellar-flutter-app` تمت للقراءة فقط. نقاط الربط المرئية حالياً:

1. `lib/screens/scan_flow_impl_normalized.dart`: نقطة اختيار V2/تمرير وسائط الالتقاط؛ تهيئة الكاميرا عبر `CameraController` بدقة `ResolutionPreset.veryHigh`، والتقاط الفيديو؛ مسار WAV يبدأ بـ`AudioEncoder.wav`, ‏16,000 Hz, قناة واحدة، وPCM 16-bit حسب تعليق إعداد الالتقاط. يُمرر thumbnail كـJPEG إلى شاشة المعالجة.
2. `lib/screens/wellar_v2_processing_screen.dart`: يقرأ bytes، ثم يرفع image ثم video ثم audio، يرسل المسح، يستطلع الحالة، ويطلب النتيجة.
3. `lib/services/wellar_v2_client.dart`: `fromSession()` يأخذ `Session.instance.accessToken`، ويضيف `Authorization: Bearer <token>` و`Idempotency-Key` عند submit. لا يرسل tenant header؛ tenant/subject من المصادقة وmembership على الخادم.
4. `lib/models/wellar_v2_result.dart` و`lib/screens/wellar_v2_result_screen.dart`: parsing وعرض النتيجة الحالية.

عقد الرفع/الـAPI المتوقع من V2 client:

| العملية | المحتوى والحقول |
|---|---|
| `POST /api/v1/media/image` | bytes صورة JPEG، `Content-Type: image/jpeg` |
| `POST /api/v1/media/video` | bytes فيديو MP4، `Content-Type: video/mp4` |
| `POST /api/v1/media/audio` | PCM WAV، `Content-Type: audio/wav` |
| `POST /api/v1/scans` | JSON: `schema_version: "1.0"`, `media_refs`, `reaction_task`, و`consent: {media_processing, consent_version, research_use}`؛ يتطلب `Idempotency-Key` ويعيد `scan_id` |
| `GET /api/v1/scans/{scan_id}` | حالة المسح |
| `GET /api/v1/scans/{scan_id}/result` | `schema_version: "2.0"`, القياسات، `behavioral_evidence`, prediction guard، والحقول القديمة |

حدود العميل المعلنة: صورة 5 MiB، فيديو 20 MiB، صوت 8 MiB. تفاصيل codec داخل MP4 تعتمد على Android camera plugin والجهاز؛ backend يقبل/يفك مسارات MP4 الموثقة بالاختبارات، لكن لا يوجد تحقق جهاز Android حقيقي هنا.

### حاجزا Flutter المكتشفان

- `WellarV2Client.submitScan` يرسل حالياً `reaction_task: null`. إذن مسار V2 الحالي لن ينتج vigilance task observations؛ سيعرض backend غياب المهمة بأمان.
- `WellarV2Result` لا يعرّف ولا يقرأ `behavioral_evidence` أو `measurements`؛ JSON الإضافي لا يكسر parser، لكنه يُهمل ولا يصل إلى واجهة الموظف. يلزم تحديث Dart model/result screen في عمل Flutter منفصل ومصرح به.
- مسار `ScanFinalizeService` القديم يرفع الملف باسم `scan_audio.m4a`. هذا غير متوافق مع endpoint V2 الذي يتطلب WAV. المسار V2 المخصص يسجل WAV كما سبق، وهو المسار المطلوب. لا ترسل AAC/M4A إلى V2 على أنه WAV ولا تعتمد على تغيير MIME type للتحويل.
- لا يوجد tenant header مطلوب أو مقبول كبديل للمصادقة. استخدم bearer token صالحاً، والخادم يحدد tenant/subject من principal.

بسبب حاجزي إرسال reaction task وإهمال parsing/العرض، فإن **تسليم المخرجات إلى التطبيق الحالي ليس مكتملاً**. لم نعدل Flutter لأن هذا القبول يمنع ذلك صراحة.

## الاختبارات والأوامر والنتائج

| الأمر | النتيجة |
|---|---|
| `rtk proxy .\\.venv\\Scripts\\python.exe -m pytest tests\\test_behavioral_engine.py::test_complete_synthetic_multimodal_scan_returns_independent_observed_outputs tests\\test_api.py::test_submit_worker_result_and_tenant_access tests\\test_api.py::test_status_and_result_are_indistinguishable_for_cross_tenant_scan_access -q` | 3 passed، تحذيران deprecation، 4.25 s، exit 0 |
| الحزمة الواسعة أدناه | 183 passed، تحذيران deprecation، 45.96 s، exit 0 |
| `rtk proxy .\\.venv\\Scripts\\python.exe scripts\\benchmark_behavioral_engine.py` | exit 0؛ 500 تكرار، تفاصيل القياس أدناه |

الحزمة الواسعة:

```text
rtk proxy .\\.venv\\Scripts\\python.exe -m pytest tests\\test_behavioral_engine.py tests\\test_dual_measurements.py tests\\test_audio_vad.py tests\\test_video_continuity.py tests\\test_synthetic_face_quality.py tests\\test_media.py tests\\test_mp4_media.py tests\\test_real_media_acceptance.py tests\\test_isolated_decode.py tests\\test_media_feature_adapter.py tests\\test_worker_inference_integration.py tests\\test_research_drowsiness_worker.py tests\\test_worker_startup.py tests\\test_api.py tests\\test_media_reconciliation.py tests\\test_hardening_media_cleanup.py tests\\test_ai_engine_foundation.py tests\\test_drowsiness_indicator.py tests\\test_drowsiness_indicator_api.py -q
```

حدث أثناء القبول فشل مؤقت في تأكيد نصي اختبر غياب `fatigue_score` بالبحث داخل JSON كله؛ وجد النص داخل explanation code `not_a_fatigue_score`، وليس كقيمة أو حقل score. عُدّل اختبار القبول ليفحص أسماء الحقول والقيم الفعلية بدلاً من مطابقة substring، ثم نجح 3/3. لم يتغير سلوك الإنتاج ولم تُضعف قيود التنبؤ.

أول تشغيل للحزمة الواسعة سجّل 182 passed و1 failed (46.34 s، exit 1) في اختبار shutdown لمصالحة حذف الوسائط؛ السبب أن timestamp إكمال المسح قد يتساوى مع cutoff، والاختبار كان يقصد اختبار checkpoint بعد حذف أول مرجع. الاختبار المعزول نجح (exit 0). ثُبتت fixture بإرجاع `updated_at` بمقدار 1 ms لضمان أن السجل أقدم بوضوح عن cutoff، مع بقاء كل التوقعات كما هي. بعدها نجح الاختبار المعزول (1 passed، 0.28 s، exit 0) والحزمة الواسعة كلها (183 passed، 45.96 s، exit 0). هذا تعديل اختبار حتمي فقط؛ لم يتغير منطق الحذف أو cutoff.

## أداء وحدود القياس

أُعيد benchmark أثناء القبول: حمولة feature ثابتة 4,785 بايت، SHA-256 `df664fe69bcd6c71c725e8c66c6e137dc892d9fa784197bcb721b0f8e25c07ff`، 20 warm-up و500 تكرار، exit 0:

- median: 0.08955 ms؛ P95: 0.2304 ms؛ maximum: 0.3713 ms.
- CPU time لـ500 إسقاط: 0.0625 s؛ ذروة `tracemalloc`: 7,446 bytes.
- المخرج المتسلسل: 8,995 bytes؛ فرق حجم input/output: 4,210 bytes.
- Python 3.10.11، Windows 10 build 26100، Intel64 Family 6 Model 158 Stepping 9، 8 logical CPUs.

هذا benchmark لإسقاط ميزات بالذاكرة فقط؛ لا يقيس decode أو upload أو worker queue أو latency الكامل أو Android. لا يثبت هدف 3–5 ثوانٍ ولا اختبار 2,000 scan.

## المخاطر وفحوص المرحلة التالية

قبل عرض النتيجة للمستخدم، يحتاج عمل Flutter مستقل إلى: إضافة parsing للحقول الجديدة وعرض التسميتين مع الشرح والامتناع؛ تمرير reaction task حقيقي إذا أصبح مدعوماً وموافقاً عليه؛ ربط consent المناسب؛ اختبار WAV وMP4 على أجهزة Android فعلية؛ وفحص حذف الوسائط والسجلات على جهاز حقيقي. يجب إبقاء `model_prediction` disabled و`prediction_extension` disabled حتى قرار تحقق علمي مستقل. لا يُستنتج من بيانات الاختبار الاصطناعية أي استعداد جهاز أو صلاحية مهنية.

## قرار القبول

- **عقد ومخرجات backend `wellar.behavioral-evidence/1.0`: PASS** — ينتج المخرجين، يُحفظ ذرياً، يُعاد عبر endpoint الصحيح، يتوافق مع FeatureEnvelope وقياسات Stage 1، يحافظ على العزل والتوافق الخلفي، ولا يصطنع score أو prediction.
- **Stage 2 practical behavioral engine كتسليم منتج end-to-end: PARTIAL** — Flutter لا يمرر reaction task ولا يقرأ/يعرض `behavioral_evidence` بعد. شرط PASS لكل بوابات التسليم غير مكتمل حتى يُنفذ الربط في عمل Flutter منفصل. لم نعدّل Flutter لأن هذا القبول يمنع ذلك صراحة.
- **Validated occupational fatigue prediction: NOT VALIDATED.**
- **Validated vigilance prediction: NOT VALIDATED.**

إذن قبول Stage 2 العملي الكامل مشروط بإنهاء حواجز Flutter في مرحلة مصرح بها لاحقاً؛ لا يعني PASS اجتياز بوابة نموذج AI العلمية الأصلية. يتوقف العمل هنا دون بدء Stage 3.
