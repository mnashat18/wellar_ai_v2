# Stage 2 — تنفيذ وتقييم النماذج الحقيقية

**الحكم المحدّث بعد مراجعة فجوة المنتج: BLOCKED — لا يوجد نموذج تعب أو يقظة يقبل خصائص Stage 1 ويدعم هدف Wellar.** نتائج Brightertime أدناه تظل تجارب بحثية صحيحة ضمن أهدافها، لكنها لا تحقق هدف النموذج المنتج. آخر تحديث: 21 أكتوبر 2026.

## الخلاصة التنفيذية

تم استخدام بيانات Brightertime المنشورة، وتقسيم المشاركين قبل التدريب إلى 40 تدريب و8 ضبط و10 اختبار لكل هدف. اتدرّب نموذج Ridge مستقل لكل هدف، مع baseline ثابت، واختيار معامل التنظيم على مجموعة الضبط فقط. الاختبار محسوب على المشاركين كمجموعات مع bootstrap مزدوج على مستوى المشارك (20,000 إعادة). البروتوكول المجمد يحدد أن الترقية تتطلب تحسن MAE لا يقل عن 10% وفاصل ثقة 95% لفارق MAE بالكامل تحت الصفر.

هذه **نماذج بحثية أحادية المصدر** تعتمد على وقت الاستيقاظ والساعة اليومية. لم يتم تدريب نموذج متعدد الوسائط؛ مجموعة Brightertime لا تحتوي على صور أو فيديو أو صوت مقترنة بنتائج المشاركين. لا يوجد نموذج إنتاجي، ولا يتنبأ المسار الحالي بدرجة تعب أو صلاحية للعمل.

## البيانات والهدف العلمي

استخدمنا نسخة البيانات الموجودة محليًا: `research/public_data/brightertime_28911977/kss.RData` و`pvt.RData`. سجل المصدر يذكر DOI `10.48420/28911977` وترخيص CC BY 4.0. تتضمن البيانات 58 مشاركًا عاملًا في المملكة المتحدة، و1,428 ملاحظة KSS معدلة و1,334 ملاحظة PVT. الدراسة تقيس النوم الذاتي وأداء مهمة قصيرة متكررة في الحياة اليومية؛ لا تمثل عينة موظفي مكاتب/خدمة عملاء قبل بدء العمل. [سجل البيانات](https://figshare.manchester.ac.uk/articles/dataset/Relationships_between_light_exposure_and_aspects_of_cognitive_function_in_everyday_life/28911977)، [الدراسة المنشورة](https://www.nature.com/articles/s44271-025-00373-9).

| مخرج البحث | الهدف الذي درّبناه فعلًا | التفسير المسموح | ما لا يعنيه |
|---|---|---|---|
| fatigue proxy | `modified_kss_sleepiness`: استجابة KSS معدلة من 1 إلى 10 | توقع بحثي للنعاس الذاتي في بيانات Brightertime | ليس مقياس تعب مهني، ولا تعب Wellar، ولا تشخيصًا |
| vigilance proxy | `brightertime_pvt_median_hit_rt`: وسيط زمن الاستجابة الصحيحة بالمللي ثانية | توقع بحثي لأداء PVT في بيانات Brightertime | ليس درجة يقظة معايرة، ولا PVT سريريًا، ولا fitness-for-duty |

الهدفان لم يُدمجا ولم تُنسب قيمة هدف من مشارك لمشارك آخر. هناك 14 قيمة `timeawake` مفقودة في المصدر؛ خط المعالجة يتعامل معها وفق إحصاءات التدريب المحفوظة في artifact، ومسار inference يرفض مدخلات غير صالحة/خارج النطاق. لا توجد معايرة ثقة فردية.

## المنهج والنتائج المقاسة

المدخلات الوحيدة: `timeawake_hours` و`dayhour_sin` و`dayhour_cos`، وفق `wellar.brightertime-context-features/1.0`. لا تستخدم هوية المشارك كميزة. التطبيع والتعويض يُقدّران من التدريب فقط؛ Alpha اختير على الضبط. الـbaseline هو وسيط قيم الهدف في التدريب. القياسات أدناه من مجموعة الاختبار، وفواصل MAE ناتجة عن bootstrap مشاركين، لا صفوف مستقلة.

### النعاس الذاتي المعدل KSS

- Ridge `alpha=0.1`: participant-macro MAE **1.2701 نقطة** (95% CI **1.0603–1.5492**)، RMSE **1.5177**.
- Baseline الوسيط: MAE **1.4877** (95% CI **1.2093–1.8357**)، RMSE **1.8663**.
- التحسن العددي النسبي: **14.63%**؛ فاصل الثقة لفارق MAE المقترن (Ridge ناقص baseline) **−0.3309 إلى −0.0784**. القاعدة العددية على هذا التقسيم متحققة.
- **لكن لا توجد مطالبة باختبار مستقل مقفول أو ترقية:** مشاركان من العشرة في اختبارنا الحالي ظهرا في اختبار KSS benchmark تاريخي موجود بالمستودع. بوابة الترقية منعت النموذج صراحةً؛ النتيجة استكشافية فقط.

### أداء PVT

- Ridge `alpha=100`: participant-macro MAE **56.2487 ms** (95% CI **39.0313–75.1516**)، RMSE **64.9703 ms**.
- Baseline الوسيط: MAE **54.6624 ms** (95% CI **41.2558–69.2947**)، RMSE **63.9629 ms**.
- التغير النسبي في MAE **−2.90%** (أسوأ من baseline)؛ فاصل الثقة لفارق MAE المقترن **−3.4993 إلى 6.9299 ms**. بوابة التحسن **فاشلة** ولا يُرقّى النموذج.

التغطية على صفوف الاختبار المؤهلة كانت 100% في هذا التقييم البحثي، لكنها لا تعني تغطية على مستخدمي Wellar. عدد المشاركين عشرة فقط في الاختبار لكل هدف؛ هذه عينة صغيرة وعدم اليقين واسع. لم نقس حساسية/نوعية/AUROC لأنها أهداف انحدار مستمرة وليست تصنيفات معرفة مسبقًا، ولم ندّعِ معايرة ثقة.

## المقارنة مع النماذج الموجودة

راجعنا الموديلات المسبقة الموجودة محليًا. MobileViT يصنف صور **نعاس السائق** إلى فئتين، وموديل DOZE-GUARD يصنف صور نعاس السائق أيضًا؛ لا يقيس أي منهما تعب موظف المكتب أو KSS أو أداء PVT. أرقام بطاقات الناشرين ليست تقييمًا مستقلًا على مستخدمي Wellar. كذلك لم تُثبت حقوق بيانات التدريب المرتبطة بما يكفي للاستخدام التجاري في Wellar. لذلك لم نستخدم هذه النماذج بدلًا من هدف Stage 2، ولم نفعّلها. تصنيف النعاس الثنائي لا يُعاد تسميته تعبًا أو يقظة.

## التوافق مع Stage 1 ومسار الاستدلال

تم إنشاء adapter بحثي محلي في `app/ml/research_proxy_adapter.py` بواجهة artifact إصدارية، والتحقق من SHA-256 والمخطط وحدود المدخلات. شُغّل بالفعل على صفوف خصائص الاختبار الحقيقية مع عدم تمرير labels إلى الاستدلال. أنتج المساران قيمة بحثية عددية موسومة `research_only_not_promoted`، والثقة `null`، و`operational_release=false`. لا تُحفظ في smoke test معرفات المشاركين أو خصائصهم أو نواتج التنبؤ.

هذه الواجهة **غير متوافقة مع envelope خصائص Stage 1**: Stage 1 يقدم خصائص صورة/فيديو/صوت ومهمة Wellar، بينما النموذجان يحتاجان وقت الاستيقاظ والساعة اليومية ضمن سياق Brightertime. لذلك `app/media/analysis.py` لا يمرر استخراج الوسائط لهذه النماذج، ويعيد لكل من التعب واليقظة سبب abstention محددًا. ملاحظة التعب الذاتية وقياس زمن الاستجابة المباشر يظلان قياسين مستقلين، وليسا توقعات AI. بوابة إصدار النموذج والإعداد الافتراضي المعطل لم يتغيرا.

## الملفات التي أضيفت أو تغيرت لهذا العمل

- `research/public_data/brightertime_28911977/REAL_MODEL_PROTOCOL_V1.md` — تعريف الهدفين وبوابة القبول المجمدة.
- `research/public_data/brightertime_28911977/real_proxy_split_v1.json` — تقسيم مشاركين حتمي، بلا تخزين أسماء/معرفات خام.
- `research/public_data/brightertime_28911977/run_real_proxy_models.py` — إعادة التدريب والتقييم وإنتاج artifact وmanifest.
- `research/public_data/brightertime_28911977/real_proxy_evaluation_v1.json` — المقاييس وفواصل الثقة ونتائج البوابات.
- `research/public_data/brightertime_28911977/models_real_proxy_v1/` — نموذجا JSON البحثيان وmanifest إصدارها.
- `research/public_data/brightertime_28911977/run_real_inference_smoke.py` — تحقق inference على خصائص حقيقية بلا labels وبلا حفظ نواتج.
- `app/ml/research_proxy_adapter.py` — تحميل/تحقق آمن للـartifacts ومحول بحثي مع abstention.
- `app/media/analysis.py` — إضافة حالة توافق النموذج/الامتناع لمخرجات Stage 1 دون تغيير الاستخراج.
- `tests/test_research_proxy_adapter.py` و`tests/test_media.py` — تغطية adapter وحالة عدم التوافق.
- `STAGE_02_REAL_MODEL_EXECUTION.md` — هذا التقرير.

بصمات مهمة: split `6bd11127677aaff2eb786d084142182bf5aab59cfc0c171632c2b31cc47e817d`؛ البروتوكول `92e806de502c47bbf3030e919ccdfcabbcdb2ce11be74ffb86ad795e2e169217`؛ artifact KSS `941bf3b0f7990537cd236c7a8732aa603bf1bdec71060a09d14dc5d10bf266f4`؛ artifact PVT `2022e7d2030e9a7ace2f7f401ca1dc5c6f16ea77caf9f2481222da4679db9977`؛ manifest `10cd26daaa1d6dbb64fe40a0ec3dc2fa81bb298a2e42bd9759306768a0661178`.

## تحقق برمجي ونتائج الاختبارات

| التحقق | النتيجة المسجلة |
|---|---|
| إعادة تدريب النموذجين وإعادة بناء المقاييس والـartifacts: `rtk proxy .\\.venv\\Scripts\\python.exe research\\public_data\\brightertime_28911977\\run_real_proxy_models.py` | **نجح، exit 0**؛ ظهرت تحذيرات تحويل RData لأنواع POSIXct/data.table، من دون فشل |
| inference smoke على صفوف اختبار حقيقية، دون تمرير الهدف: `rtk proxy .\\.venv\\Scripts\\python.exe research\\public_data\\brightertime_28911977\\run_real_inference_smoke.py` | **نجح، exit 0**؛ النموذجان بحثيان وغير مفعّلين، الثقة null، لا تُحفظ قيم المشاركين/النواتج |
| اختبارات Stage 2/Stage 1 وAPI وworker وmanifest/تأهيل البيانات: `rtk proxy .\\.venv\\Scripts\\python.exe -m pytest tests\\test_research_proxy_adapter.py tests\\test_media.py tests\\test_api.py tests\\test_worker_inference_integration.py tests\\test_worker_startup.py tests\\test_research_manifest_validation.py tests\\test_dataset_qualification.py -q` | **90 passed، تحذيران، 32.07 ثانية، exit 0** |
| المجموعة الأوسع للوسائط وAPI والـworker والأمان والتخزين والبيانات البحثية، 22 ملف اختبار محددًا | **274 passed، إخفاق واحد، 3 تحذيرات، 93.17 ثانية، exit 1**. الإخفاق `test_synthetic_s3_delete_concurrency_resource_experiment` بسبب انتهاء مهلة عملية حذف محلية أثناء تشغيل المجموعة؛ لا يتعلق بتدريب أو adapter Stage 2. |
| إعادة الاختبار المعزولة: `rtk proxy .\\.venv\\Scripts\\python.exe -m pytest tests\\test_s3_storage.py::test_synthetic_s3_delete_concurrency_resource_experiment -q` | **1 passed، تحذيران، 4.21 ثانية، exit 0**. هذا يرجح حساسية توقيت/تداخل في تجربة S3، لكنه لا يمحو إخفاق المجموعة الأوسع. لم تُعدّل توقعات الاختبار. |

هذه أدلة على إعادة التدريب وسلامة البرمجيات والعقد؛ **ليست** إثباتًا علميًا للصلاحية المهنية أو الطبية. التشغيل الكامل استخدم البيانات البحثية المحلية، أما اختبارات الوسائط فتستخدم أيضًا synthetic media للتحقق الوظيفي فقط، وليس لتدريب أو تقييم علمي.

## ما يلزم قبل الإغلاق العلمي أو الاستخدام التشغيلي

1. مجموعة مستقلة، مرخصة للاستخدام المقصود، فيها مشاركون موظفون من بيئة مكتبية/خدمة عملاء، وقياس تعب مهني معرّف مسبقًا، وقياس يقظة/أداء مرجعي مناسب في نافذة قبل العمل.
2. بيانات متزامنة ومقترنة للمشارك نفسه إذا كان المطلوب إثبات دمج صورة/فيديو/صوت/مهمة؛ لا تصلح بيانات Brightertime لهذا الدمج.
3. تقسيم participant-disjoint جديد غير مستخدم سابقًا، واختبار مقفول مستقل لم يتعرض له الباحثون/التجارب القديمة، ثم معايرة وثبات وتقييم نطاق التغطية والامتناع على المجتمع المقصود.
4. معايير قبول علمية متفق عليها مسبقًا ومراجعة مناسبة قبل أي استخدام يخص الموظفين. لا توجد هنا ادعاءات تشخيص أو لياقة للعمل أو مخاطر.

**قرار تجربة Brightertime وحدها:** PARTIAL. يوجد تدريب حقيقي ومحول بحثي يعمل على سياق بيانات Brightertime، لكن KSS هدف نعاس ذاتي واختباره تعرض تاريخيًا، وPVT لم يتجاوز baseline. لا يوجد تقدير متوافق مع ميزات Stage 1.

## ملحق إغلاق فجوة موديل المنتج — 21 أكتوبر 2026

هذا الملحق لا يعيد تدريب أو تقييم Brightertime. راجعنا الملفات المحلية وحقوق/وصول المصادر الأصلية لاختيار أقصر مسار مشروع لنموذج يستخدم مدخلات Stage 1. **لم ننزل أو نعالج أي مجموعة جديدة، ولم ندرب أي artifact جديد**؛ لم يظهر مصدر يجمع في آن واحد حق الاستخدام، هدفًا صالحًا، توافق media/feature، وإمكانية تنزيل مؤكدة.

### جرد البيانات والـartifacts المحلية

- `research/public_data/brightertime_28911977/`: ثمانية ملفات RData وملفات CSV مشتقة/مرافقة. فيها KSS/PVT ومهام معرفية لمشاركي الدراسة، بلا ملفات وجه/فيديو/صوت مقترنة. لا يمكن منها تعلّم تحويل خصائص Stage 1 إلى KSS أو PVT.
- `research/public_data/attentionbase/data.csv` (حوالي 7.9 MB): لا يحتوي معرفات مشاركين موثوقة بحسب تأهيل البيانات السابق، ولا media خام متزامنة؛ غير مؤهل لتقسيم المشارك ولا لتدريب مسار Stage 1.
- `data/private-pilot/session-001/media/`: صورة JPG وفيديو MP4 وصوت WAV محليًا لجلسة واحدة. بيانات الحقوق نفسها تسجل `evaluation=false` و`model_training=false`، و`rights.json` لا يثبت الترخيص أو موافقة التدريب. استخدمت هذه العينة لاستخراج خصائص محلي فقط حسب نطاق الموافقة؛ ليست مجموعة تدريب أو اختبار، ولم تُستخدم لتقدير أداء موديل.
- `models/pretrained/`: ملفا وزن فقط: MobileViT drowsy/non-drowsy على صور سائقين (MIT معلن للموديل، حقوق بيانات التدريب غير مثبتة تجاريًا) وDOZE-GUARD drowsy/non-drowsy لصورة سائق (Apache-2.0 للموديل، ترخيص مجموعة التدريب غير معلن). لا يملك أي منهما target لـKSS أو fatigue مهني أو PVT، ومدخلهما صورة خام بدل Stage 1 `FeatureEnvelope`. أرقام الناشر ليست أداءً محققًا لدينا؛ لا اعتماد أو تفعيل.
- لا توجد checkpoints أخرى بصيغ PT/ONNX/Safetensors غير هذين الملفين في `models/`. موجودة artifacts Brightertime JSON البحثية السابقة فقط، وليست موديلات media.

### ترتيب مجموعات البيانات المرشحة وإمكانية استخدامها

| الترتيب العملي | المصدر والهدف | المشاركون/المراجع/الوسيط | الوصول والحقوق المتحققة | القرار |
|---|---|---|---|---|
| 1 للهدف المهني، لكن غير متوافق مع المدخلات | MEFAR: تعب ذهني مقاس بـChalder Fatigue Scale، جلسات صباح/مساء | 23 مشاركًا، EEG وBVP وEDA وHR وحرارة وACC؛ لا وجه/فيديو/صوت | صفحة Mendeley تعرض Version 5 وCC BY 4.0 وزر تنزيل، لكن تنزيل الملف الفعلي لم يتحقق هنا؛ وحتى لو نزلت، لا توجد modality من Stage 1 مقترنة به | أقرب مرجع منشور للتعب الذهني، لكنه لا يدرب موديل Wellar media. لا artifact أو inference منه. [سجل Mendeley](https://data.mendeley.com/datasets/z3g26tphnv/5) |
| 2 لموديل drowsiness بحثي فقط | UL-DD: KSS ذاتي كل 4 دقائق في محاكاة قيادة، مع وسائل حيوية وأداء قيادة | 19 مشاركًا/35 جلسة تقريبًا، RGB/IR/3D وposture وbiometrics وtelemetry؛ KSS نعاس سائق، لا تعب موظف | سجل Zenodo رسمي، لكن الملفات مقيدة ولا يمكن تنزيلها دون حساب وموافقة مؤسسية/بحثية. اتفاق الاستخدام يسمح بأبحاث علمية (ومنها R&D تجاري ضمن غرض البحث) ويمنع إعادة التوزيع والإعلان ويقصر الاستخدام على البحث العلمي؛ لم يُطلب الوصول. المصدر يذكر حجم 9.9 TB. [السجل والاتفاق](https://zenodo.org/records/17978727) | أفضل مرشح media قريب نسبيًا، لكنه غير متاح لنا حاليًا والهدف نعاس سائق. لا يمكن الادعاء بتوافق FaceMesh/Stage 1 قبل عينة مصرح بها. لا تدريب. |
| 3 لقياس نعاس/يقظة سائق، مرفوض للمنتج | DROZY: KSS وPVT وPSG مع فيديو وجه NIR | 14 مشاركًا؛ 1–9 KSS، أزمنة PVT، PSG، MP4 رمادي NIR، landmarks وتوقيت؛ لا صوت | ORBi يعرض أرشيفًا 2.46 GB واتفاقًا منفصلًا. رابط الملف موجود لكن أداة التصفح لم تستطع جلب ZIP، ولم ينزل محليًا. الأهم أن الاتفاق يحصر الاستخدام في البحث/التقييم ويمنع تطوير المنتجات التجارية صراحةً. [التوثيق](https://www.drozy.ulg.ac.be/)، [الاتفاق](https://orbi.uliege.be/bitstream/2268/191620/5/DROZY-LicenseAgreement-20180718.pdf) | لا يصلح لتطوير منتج Wellar. 14 participant clusters صغيرة، والـNIR لا يساوي RGB؛ لا compatibility run. |
| 4 لنعاس فئوي بوسيط RGB، مرفوض حتى إثبات الحقوق | UTA-RLDD: فئات alert / low vigilance / drowsy من KSS-guided self-labels | 60 شخصًا، 180 RGB videos (~30 ساعة)، 3 تسجيلات لكل شخص، folds حسب المشاركين | صفحة المالك تعرض رابط Google Drive؛ فتح الرابط أعاد صفحة تفاعلية ولم يثبت تنزيل الملفات. لا توجد رخصة تجارية منشورة في الصفحة. [صفحة المالك](https://sites.google.com/view/utarldd/home) | لا يجوز استخدامه قبل إذن حقوق صريح. التسمية تعبر عن نعاس/يقظة ذاتية في سياق قيادة، وليست تعبًا مهنيًا ولا مرجع PVT. |
| 5 احتمال fatigue بصري لا يمكن التحقق منه | DLFD / Daily-life Fatigue Dataset | الورقة تصف 32 متطوعًا و375 فيديو 5 ثوانٍ–دقيقتين. تفاصيل استقلال مرجع التعب وملفات المشاركين/تقسيماتهم وحقوق الاستخدام لم تتأكد من سجل موزع | لم نجد مستودع بيانات/رخصة أو رابط تنزيل أصلي يمكن التحقق منه؛ وصف الورقة وحده لا يثبت الوصول أو صلاحية labels. [الورقة](https://arxiv.org/abs/2104.10420) | لا تنزيل ولا تدريب ولا إعادة استخدام لأرقام المنشور. يحتاج تصريح المؤلفين، ملفات participant IDs، تعريف label وحقوق تجارية مكتوبة أولًا. |
| 6 لنعاس صوتي بحثي فقط | SLC / SLEEP / Objective and Subjective Sleepiness Voice corpora: تسجيلات صوت ودرجات KSS | SLC نحو 99 متحدثًا، وSLEEP نحو 915 متحدثًا؛ KSS صوتي وليس تعبًا مهنيًا. SLEEP مقاطع قصيرة ~4 ثوانٍ | المراجع تصف إتاحة عبر تحديات/اتفاقيات، ولم نعثر على رخصة مفتوحة تسمح بتطوير تجاري أو تنزيل مباشر غير مشروط لهذه الأغراض. [مراجعة corpora](https://www.frontiersin.org/journals/digital-health/articles/10.3389/fdgth.2021.686068/full)، [إعلان SLC](https://auditory.org/postings/2011/242.html) | مرشح بحثي للصوت فقط بعد عقد/إذن وتقييم تقسيم speaker-disjoint. Stage 1 الحالي يصدر signal quality وVAD heuristic، لا خصائص prosody/voice مطابقة لنماذج النوم المنشورة. لا تدريب قبل الوصول وتوثيق الترخيص. |

صفحة PAAWS الحالية تصف wearable/phone/PSG ومقاطع فيديو تُستخدم لإنتاج annotations، لكن الفيديو الخام غير منشور في الوصف، ولا يوجد صوت مسجل في قنوات PSG المذكورة؛ كما أن رابط Repository أعاد 403 في التحقق المتاح. لذلك لا تربط وسائط Stage 1 بمراجع تعب/يقظة هناك. [صفحة البيانات والوصول](https://www.paawsstudy.org/data.html). مجموعة DMD متعددة الكاميرات فيها annotations لسلوكيات قيادة مثل التثاؤب والـmicrosleep، لكن رخصتها CC BY-NC-ND ومخصصة للاستخدام الأكاديمي فقط؛ لا تؤهل لتطوير المنتج التجاري. [المصدر](https://dmd.vicomtech.org/).

### توافق ميزات Stage 1 والمسار القصير الممكن

الكود الحالي يخرج خصائص وصفية فعلية لكل وسيط، لكنه لا يملك موديلًا يتعلم منها target مستقلًا. صورة JPG تعطي جودة/وضوح وإضاءة وقياسات هندسية landmarks عند تحقق شروط الوجه. فيديو MP4/AVI يعطي قياسات زمنية sparse للوجه/العين/الفم مع فحوص continuity وجودة؛ لا يثبت هوية الوجه. الصوت يقبل PCM WAV ويعطي signal metrics و`speech_like_activity_heuristic`، وليس له prosody/MFCC أو speech transcription ولا VAD مُثبت على حديث بشري. هذه proxies لا يمكن نسخ أوزان driver/image/audio عامة عليها ثم تفسيرها كتعب مهني.

من تقرير Stage 1، فحص العميل Flutter للقراءة فقط وجد thumbnail بصيغة JPEG، فيديو يُجهز كـMP4 (مع ضرورة فحص الحاوية عند فشل normalization)، وصوت AAC-LC mono 44.1 kHz بصيغة `.m4a`. backend لا يقبل M4A/AAC حاليًا؛ الهاندأوف المطلوب لأي دراسة مستقبلية تحويله إلى PCM WAV مدعوم مع metadata/timestamps. لم يتغير Flutter.

أقصر جمع مشروع، دون تغيير العميل في هذه المهمة:

1. قبل الجمع: اعتماد هدفين منفصلين من باحث تعب مهني/قياس نفسي، وتأكيد حقوق النسخة/التطبيق التجاري، موافقة أخلاقيات وخصوصية وموافقة منفصلة صريحة لكل من image/video/audio والتدريب والاحتفاظ/الحذف. خيار OFER-15 Intershift Recovery الذي ظهر في المراجعات السابقة يظل **مرشحًا فقط** لحين تأكيد النسخة العربية وملاءمة ما قبل الوردية وحقوقها. اجمع KSS منفصلًا للنعاس ولا تسمّه fatigue.
2. لكل موظف مشارك، اربط pseudo-ID ثابتًا داخل الدراسة مع عدة جلسات قبل الوردية وعلى أيام مختلفة. اجمع المقياس المعتمد للتعب قبل التصوير/الميديا، ثم media session واحدة متزامنة محفوظة بوقت البدء/الانتهاء وإصدار كل extractor. لا تسمح موافقة media-analysis وحدها بالتدريب: العينة المحلية الحالية ترفض التدريب والتقييم صراحةً.
3. لليقظة، استخدم PVT مرجعيًا محدد البروتوكول ومؤكد التوقيت بعد التقاط media. إذا كان نموذج المستقبل يستخدم reaction task كميزة، اجعل مرجع التقييم PVT مستقلًا عنه لتجنب تسريب الهدف. قياس Wellar القصير يظل observation منفصلًا ما لم تتم معايرته مقابل المرجع.
4. اخزن raw trial timestamps وجودة الجهاز وحالة الإكمال والوسائط المفقودة. اجعل participant IDs كلها في split واحد؛ خصص مجموعة اختبار غير مكشوفة لدى أمين بيانات، وحدد حجم العينة بتحليل قدرة/دقة قبل الاطلاع على الأداء. قيم media-only، كل وسيط منفردًا، وfusion فقط مع pairing حقيقي. لا تجعل self-report المستخدم كهدف يدخل كميزة للنموذج نفسه.
5. client الحالي يرسل JPEG وMP4 وM4A/AAC؛ على خط الدراسة تمرير image/video كما يدعمهما backend، وفك/تحويل audio محليًا إلى PCM WAV المقبول (1–2 channels, 8–96 kHz, 8/16/24/32-bit) مع حفظ أصل المصدر والـtimestamps والإقرار بالتحويل. لا تدّعِ أن نموذجًا جاهزًا يستهلك M4A.

### S3: فحص التقطع المتكرر

لم يتغير كود S3 أو شروط الاختبار. الاختبار المحدد `test_synthetic_s3_delete_concurrency_resource_experiment` شُغّل منفردًا خمس مرات: **5/5 نجاح، كل مرة exit 0**. كل تشغيل يستخدم 6 طلبات إلى HTTP server محلي اصطناعي، وحد التزامن 2، بلا تخزين فعلي. الزمن الكلي المقاس: **4.731، 3.996، 4.058، 4.147، 4.250 ثانية**؛ peak HTTP/processes كان 2 بكل مرة، RSS child منفرد 4.2–4.3 MiB، aggregate 8.4–8.5 MiB.

في المجموعة الأوسع السابقة فشل نفس الاختبار مرة عند حد مهلة إجمالي 5 ثوانٍ بينما استغرق setup/انتظار semaphore جزءًا من الميزانية، ثم نجح عند تشغيله منفردًا. هذا يثبت حساسية اختبار التكامل لتأخير تشغيل subprocess تحت تزاحم الاختبارات؛ لا يثبت فشلًا دائمًا في S3 ولا يبرر توسيع timeout أو إضعاف assertion. لا يمكن ضمان bounded completion تحت انشغال CPU أعلى من الافتراض الحالي؛ يظل هذا حدًا تشغيليًا يحتاج مراجعة أداء منفصلة، خارج طلب عدم تشغيل benchmark عالي الحمل.

### تحقق هذا الإغلاق

| الأمر/الفحص | النتيجة |
|---|---|
| `rtk proxy .\\.venv\\Scripts\\python.exe -m pytest tests\\test_research_proxy_adapter.py tests\\test_media.py tests\\test_media_feature_adapter.py tests\\test_real_media_acceptance.py tests\\test_audio_vad.py tests\\test_video_continuity.py tests\\test_api.py tests\\test_worker_startup.py tests\\test_worker_inference_integration.py -q` | **89 passed، تحذيران deprecation، 60.74 ثانية، exit 0** |
| `rtk proxy .\\.venv\\Scripts\\python.exe -m pytest tests\\test_s3_storage.py::test_synthetic_s3_delete_concurrency_resource_experiment -q -s` (خمس عمليات منفصلة) | **5/5 passed، كل exit 0**؛ القياسات أعلاه |
| المجموعة الأوسع من الوسائط وAPI والـworker والأمان والتخزين والبيانات البحثية المسجلة في المحاولة السابقة بالتقرير | **274 passed، إخفاق S3 واحد، 3 تحذيرات، 93.17 ثانية، exit 1**. إعادة S3 منفردة نجحت. لا نمسح هذا الإخفاق بالنجاح المعزول. |
| إعادة Brightertime أو تنزيل/تدريب بيانات جديدة | **لم تُجرَ** حسب النطاق؛ لا نكرر التجربة السابقة، ولا ملف محلي مؤهل لتدريب مدخلات Stage 1. |

### أحكام الجاهزية النهائية

- **Fatigue media model: BLOCKED.** لا يوجد dataset محلي يجمع media Stage 1 بمرجع تعب مهني، ولا model artifact بهذه الدلالة. MEFAR يملك هدفًا أقرب لكنه physiologic فقط، وعينة private pilot تمنع التدريب والتقييم.
- **Vigilance media model: BLOCKED.** لا يوجد paired media + PVT مرجعي مرخص ومتاح ومتوافق. Reaction-task وPVT القياسات المباشرة ليست توقع AI؛ DROZY/UTA-RLDD targets نعاس/يقظة قيادة وحقوق الوصول/الاستخدام لا تسمح حاليًا بموديل Wellar.
- **Stage 1 extraction software: PASS للاستخراج الموصوف في تقرير Stage 1، وليس كتنبؤ تعب/يقظة.** اختبار regression الحالي مرّ 89/89.
- **Stage 1-compatible research inference: BLOCKED للهدفين.** adapter Brightertime يحتاج `timeawake/dayhour` ولا يأخذ media envelope؛ اختبارات السلوك الحالي تتأكد من abstention. لا أُنشئ adapter شكلي يغير معنى الهدف.
- **Production release: NOT AUTHORIZED / disabled.** لا model يحقق جودة target أو توافق المدخلات أو حقوق الاستخدام والاختبار المقفول. حواجز التعطيل لم تتغير.
- **Stage 2 overall: BLOCKED بالنسبة لمهمة بناء تقديري AI حقيقي من وسائط Wellar.** البرمجيات والقياسات الحالية تعمل، والتجارب البحثية السابقة موثقة، لكن العائق الحاسم بيانات/حقوق/مرجع متزامن. أقصر انتقال هو موافقة دراسة موظفين منفصلة وجمع paired media + fatigue instrument + PVT مستقل؛ بعدها فقط تدريب participant-disjoint وتقييم اختبار مقفول بمعايير مسبقة.


## مراجعة جدوى النماذج المرئية المجانية (21 أكتوبر 2026)

### القرار

لا يوجد نموذج مجاني متاح حاليًا يجمع بين حق استخدام واضح، هدف يطابق تعب/يقظة موظفي Wellar، ومدخلات Stage 1. أبقي التنبؤ مغلقًا. أقوى بديل آمن موجود هو استخراج ملاحظات قصيرة وصفية من MediaPipe/OpenCV، مع امتناع منفصل لكل من fatigue وvigilance دون قيمة AI.

### Fatigue-Sense

راجعت [المستودع](https://github.com/Fatigue-Sense/fatigue-sense) و[تعليمات التدريب](https://github.com/Fatigue-Sense/fatigue-sense/blob/main/docs/training.md) وصفحات [eye classifier](https://huggingface.co/FatigueSense/eye_classifier/tree/main)، [pose model](https://huggingface.co/FatigueSense/pose_model/tree/main)، [temporal model](https://huggingface.co/FatigueSense/temporal_model/tree/main)، و[temporal dataset](https://huggingface.co/datasets/FatigueSense/temporal_dataset).

المستودع يصف خطًا يقص الوجه ويصنف العين والفم ويقدر وضعية أعلى الجسم، ثم يجمع 17 خاصية عند 1 Hz لنافذة 60 ثانية ويمرر 30 خطوة إلى BiGRU لإخراج focus score من 0 إلى 1. النتيجة الافتراضية الأولى تتطلب نحو 90 ثانية؛ حتى وضع smoke بنافذة 5 ثوانٍ ينتظر قرابة 35 ثانية. المخرج المعلن هو focus score، وليس fatigue أو KSS أو PVT، ولا يناسب مسح Wellar من 3–5 ثوانٍ.

| الأصل | الوظيفة المعلنة | قرار Wellar |
|---|---|---|
| eye classifier، 4.13 MB؛ mouth classifier، 4.13 MB | تصنيف ROI إلى مفتوح/مغلق | يحتاج قصًا ومعالجة خاصة، ولا يظهر ترخيص منفصل للوزن أو dataset؛ لم أنزلهما أو أشغلهما. |
| pose model، 5.62 MB | YOLO pose لخمس نقاط | لا يطابق envelope مباشرة؛ ملف pt معلن كحاوية pickle وبها imports خاصة بـUltralytics؛ لم أشغله. |
| temporal model، 449 kB مع normalization/config | focus score من خصائص زمنية | يحتاج نافذة طويلة ولا يملك مرجعًا مستقلاً مناسبًا؛ ملف pt معلن كـpickle؛ لم أشغله. |

ترخيص كود GitHub معلن MIT، لكن README يحيل ترخيص الأوزان والبيانات إلى صفحات Hugging Face. صفحات النماذج لا تعرض model card أو ترخيصًا منفصلًا للأوزان، وصفحات مجموعات البيانات بلا dataset card أو معلومات حقوق/مصدر/مشاركين كافية لإثبات الاستخدام التجاري. صفحات Hub تسرد وجود الملفات وأحجامها، لكنها لا تثبت integrity بتنزيل محلي أو حق الاستخدام. لم أنزّل الأوزان أو أشغّل كود upstream، ولم أتحقق من hash محلي.

بيانات العين/الفم صور ROI بوسوم open/closed، وليست ground truth للتعب. pose dataset يحوي نقاط/وسوم pose. temporal dataset يعرض 53,757 صفًا من خصائص مثل blink/yawn/head pose، دون KSS/PVT أو fatigue labels مستقلة، ودون participant/session identifiers ظاهرة في schema. وثائق التدريب تقول إن labels النوافذ الزمنية تُبنى داخل dataset class. لا يوجد دليل كاف على فصل المشاركين أو اختبار مستقل مقفول؛ لم أستخدمها لتدريب أو تقييم Wellar.

### مقارنة الأوزان المحلية

- MobileViT v2 محليًا: 69,935,051 بايت، SHA-256: fcbe35c8e0c8149bed84189ab3cf0a06429107a968667a9f681ff113bed35867. مسار الاختبار المحلي يستخدم تحميل PyTorch المقيد weights_only ويتحقق من الحجم والتجزئة. مخرجه drowsy/non_drowsy لصورة سائق واحدة؛ ليس تعب موظفين أو يقظة/PVT. ترخيص الموديل معلن MIT، لكن حقوق dataset التدريب غير مثبتة للاستخدام التجاري.
- DOZE-GUARD-RLDD محليًا: safetensors بحجم 371,567,992 بايت، SHA-256: 76dab6f2031a0ef0081ccf86b7d6c9b9c4a3f4d1a15f3eecb6019c1c99639a5b. التنسيق يتجنب pickle، لكن المخرج يظل تصنيف صورة سائق Drowsy/Non Drowsy؛ ترخيص بيانات التدريب غير ظاهر في provenance المحلي. لم أشغله لهذا المسار.
- كلاهما يقبل صورة خام ثابتة فقط، وليس Stage 1 FeatureEnvelope. صورة واحدة حد استدعاء تقني وليست مدة رصد علمية؛ لا أعيد تسمية نتيجتهما fatigue أو vigilance.

### البديل الآمن ووقت التنفيذ

Stage 1 يستخرج أوصافًا فقط: تغطية الفيديو والوجه، أحداث إغلاق العين/الرمش ومددها، نسبة الإغلاق في المقطع القصير، فتح الفم، yaw/pitch، وجودة الصورة/الضوء/الحدة واستمرارية مكانية محدودة. هذه ليست PERCLOS معتمدة ولا تصنيف نعاس. الصوت لا يتحول إلى fatigue evidence. fatigue وvigilance يظلان هدفين منفصلين بقيمة null وسبب امتناع. قياس التقرير الذاتي وملاحظة reaction task منفصلان عن AI.

أزمنة Stage 1 السابقة كانت على وسائط اصطناعية محلية: صورة نحو 507 ms شاملة تهيئة MediaPipe؛ فيديو synthetic مدته ثانيتان/8 إطارات 461.455 ms إجماليًا و33.725 ms decode؛ WAV نحو 21.258 ms. هذه أزمنة extraction لا تشمل نموذجًا، ولا تمثل benchmark أو ضمانًا لمسح 3–5 ثوانٍ. سجل private pilot يسمح بتحليل محلي لكنه لا يسمح بالتدريب أو التقييم؛ لم أختبر نماذج على أشخاص.

### اختبار هذا الإغلاق

الأمر: rtk proxy .\.venv\Scripts\python.exe -m pytest tests\test_research_image_drowsiness.py tests\test_research_drowsiness_worker.py tests\test_media.py tests\test_worker_inference_integration.py tests\test_ai_engine_foundation.py -q

النتيجة: 55 passed، تحذيران deprecation، 45.81 ثانية، exit 0. شملت الاختبارات integrity/رفض العبث، disabled-by-default، عقود worker/API واستخراج الوسائط؛ هي اختبارات برمجية وليست validation علمية. تنزيل وتشغيل Fatigue-Sense وDOZE-GUARD لم يحدث. MobileViT المحلي اختبره smoke برمجي معزول فقط.

### أحكام الجاهزية

- Fatigue AI المرئي: BLOCKED؛ لا dataset/target/حقوق وأوزان مناسبة.
- Vigilance AI المرئي: BLOCKED؛ لا مرجع vigilance مستقل مقترن، ونماذج نعاس السائق لا تقيس PVT الموظف.
- Software fallback/extraction: PASS؛ الملاحظات الوصفية واختبارات البرمجيات تعمل، بلا تنبؤ.
- Stage 1-compatible AI inference: BLOCKED للهدفين؛ النظام يمتنع بقيمة null وسبب صريح.
- Production release authorization: NOT AUTHORIZED.
- Stage 2 لبناء تقدير AI مرئي حقيقي: BLOCKED. إعادة التقييم تتطلب إثبات حقوق الاستخدام، وبيانات موثقة بهوية مشارك/جلسة ومرجع fatigue/vigilance مستقل، ونافذة مناسبة، ثم تقييمًا participant-disjoint بمعايير مسبقة. لم يبدأ Stage 3.
