# -*- coding: utf-8 -*-
"""
Batch backfill script for the 10 Meta Hot Posts evaluation.
Runs purely locally but connects to remote production database via environment variables.
"""
from __future__ import annotations

import sys
import json
from pathlib import Path

# Ensure utf-8 encoding output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from appcore.meta_hot_posts import store

# The 10 evaluated posts data
EVALUATIONS = {
  44245: {
    "us": {
      "overall_score": 72,
      "copyability_score": 40,
      "meta_us_ad_fit_score": 75,
      "product_fit_score": 95,
      "compliance_risk_score": 90,
      "recommendation": "avoid",
      "summary": "High quality motorsport footage showcasing the raw speed and thrill of the Isle of Man TT. Highly engaging but extremely low copyability due to exclusive digital broadcasting rights and licensing barriers.",
      "summary_zh": "高画质的摩托车赛事混剪，展现了曼岛TT的极致速度与惊险刺激。虽然互动表现优异，但由于独家数字转播权和版权门槛，极难被普通卖家复制。",
      "winning_angles": ["Extreme sports thrill hook", "Exclusivity and live FOMO"],
      "copy_notes": ["Niche high-speed editing style", "Strong background engine roar sound design"],
      "risk_notes": ["Severe IP and copyright violation risk if broadcasting rights are not owned"]
    },
    "eu": {
      "suitability_score": 85,
      "recommendation": "adapt_before_translation",
      "direct_reuse": False,
      "translation_fit_score": 75,
      "best_countries": ["Germany", "France", "Italy", "Spain"],
      "country_scores": {"GERMANY": 88, "FRANCE": 84, "ITALY": 86, "SPAIN": 80},
      "source_language_detected": "English",
      "speech_dependency": "low",
      "on_screen_text_dependency": "low",
      "needs_subtitle_translation": True,
      "needs_voiceover_or_dubbing": False,
      "needs_screen_text_replacement": True,
      "localization_difficulty": "low",
      "country_localization_notes": {
        "Germany": ["High interest in motorsports, need local streaming schedule details"],
        "France": ["Requires localized pricing and payment methods in Euros"]
      },
      "strengths": ["High adrenaline visuals that transcend language barriers", "Established European fanbase for motorsport"],
      "risks": ["Strict licensing laws in European territories", "High localization costs for multiple languages"],
      "required_changes": ["Translate schedule overlays", "Add localized CTA for European payment options"],
      "reasoning": "The video relies heavily on high-adrenaline racing action which is universally understood. However, targeting European audiences requires localized schedules, race timings (local time zones), and local pricing.",
      "strengths_zh": ["超越语言障碍的高刺激性视觉效果", "欧洲地区深厚的赛车运动粉丝基础"],
      "risks_zh": ["欧洲各地区严格的赛事授权法律", "多语种本地化的运营成本较高"],
      "required_changes_zh": ["翻译赛事日程表等屏幕覆盖层", "添加针对欧洲本地支付选项的本地化引导"],
      "reasoning_zh": "视频主要依靠极具视觉张力的赛车画面，这种肾上腺素飙升的感觉是通用的。然而，要真正打动欧洲受众，需要翻译当地时区的赛程表并支持欧元等本地货币计价。"
    }
  },
  44240: {
    "us": {
      "overall_score": 88,
      "copyability_score": 90,
      "meta_us_ad_fit_score": 92,
      "product_fit_score": 95,
      "compliance_risk_score": 95,
      "recommendation": "copy",
      "summary": "Perfect examples of a problem-solution ad. Strong 3-second hook showing the pain of traditional mops versus the ease of the self-wringing flat mop. Very high replication potential.",
      "summary_zh": "完美的“发现痛点-解决问题”广告模板。开场3秒强烈对比了传统拖把的费力与免手洗平板拖把的便捷，展示极其直观，可复制性极高。",
      "winning_angles": ["Pain point contrast (bending over vs upright cleaning)", "Functional demonstration (self-wringing mechanism)"],
      "copy_notes": ["Start with a frustrating cleaning scenario", "Show clear side-by-side or before-after cleaning efficacy"],
      "risk_notes": ["Ensure claims about 'works on all floors' match actual product capability to avoid chargebacks"]
    },
    "eu": {
      "suitability_score": 90,
      "recommendation": "translate_and_launch",
      "direct_reuse": False,
      "translation_fit_score": 90,
      "best_countries": ["Germany", "France", "Italy", "Spain"],
      "country_scores": {"GERMANY": 92, "FRANCE": 90, "ITALY": 88, "SPAIN": 87},
      "source_language_detected": "English",
      "speech_dependency": "medium",
      "on_screen_text_dependency": "high",
      "needs_subtitle_translation": True,
      "needs_voiceover_or_dubbing": True,
      "needs_screen_text_replacement": True,
      "localization_difficulty": "low",
      "country_localization_notes": {
        "Germany": ["Focus on efficiency, ecological durability (washable pads)", "CE compliance and solid build quality details"],
        "France": ["Highlight eco-friendly aspects and ergonomic back protection benefits"]
      },
      "strengths": ["Universal problem solver with clear visual messaging", "Microfiber pads match European preference for sustainable cleaning"],
      "risks": ["High competition in the home supplies category in Europe", "Language barrier in ad copy and overlay texts"],
      "required_changes": ["Translate all bulleted overlays into German, French, Italian, Spanish", "Add localized voiceover using high-quality local TTS"],
      "reasoning": "This household item solves a universal chore pain point. Highly suited for European markets, but demands translated screen text and localized copy due to high dependence on text overlays showing mop benefits.",
      "strengths_zh": ["具有清晰视觉传达的普适性痛点解决方案", "超细纤维清洁垫符合欧洲人对可持续清洁的偏好"],
      "risks_zh": ["欧洲家居用品类目竞争激烈", "广告文案和视频内覆盖文字存在语种壁垒"],
      "required_changes_zh": ["将所有卖点文字覆盖层翻译为德语、法语、意大利语、西班牙语", "使用高品质本地语言配音替换英文配音"],
      "reasoning_zh": "这款家居清洁用品解决了全球家庭的共同家务痛点。非常适合欧洲市场，但因为视频高度依赖文字覆盖层来展示产品卖点，测试前必须完成文字和配音的本地化翻译。"
    }
  },
  44227: {
    "us": {
      "overall_score": 78,
      "copyability_score": 85,
      "meta_us_ad_fit_score": 80,
      "product_fit_score": 90,
      "compliance_risk_score": 30,
      "recommendation": "adapt",
      "summary": "Highly engaging ad targeting male fatigue and wellness. However, the pseudoscience claims ('ground energy', 'restore masculine drive') carry a high risk of ad account disabling on Meta US if flagged. Needs careful adaptation of claims.",
      "summary_zh": "针对男性疲劳和能量流失的极其吸睛的广告。但其声称的“接地能量”、“恢复男性原动力”等伪科学宣称存在极高的Meta封户风险。必须弱化此类绝对化的功效承诺再做复制。",
      "winning_angles": ["Targeting afternoon energy crash (2pm fatigue)", "Masculine 'Warrior Stone' positioning"],
      "copy_notes": ["Keep the aesthetic of raw hematite beads", "Refocus messaging from physical/biological claims to mental focus and accessory styling"],
      "risk_notes": ["High risk of account suspension due to medical/pseudoscience policy violations", "Extremely low comment-to-like ratio suggest heavily filtered comments or artificial engagement"]
    },
    "eu": {
      "suitability_score": 68,
      "recommendation": "adapt_before_translation",
      "direct_reuse": False,
      "translation_fit_score": 70,
      "best_countries": ["Germany", "France", "Italy", "Spain"],
      "country_scores": {"GERMANY": 60, "FRANCE": 65, "ITALY": 72, "SPAIN": 74},
      "source_language_detected": "English",
      "speech_dependency": "medium",
      "on_screen_text_dependency": "high",
      "needs_subtitle_translation": True,
      "needs_voiceover_or_dubbing": True,
      "needs_screen_text_replacement": True,
      "localization_difficulty": "high",
      "country_localization_notes": {
        "Germany": ["Strictest regulations. Under UWG, claims about healing/energy properties of stones are subject to severe fines. Must market purely as fashion accessory."],
        "France": ["Requires soft-pedaling energy claims to avoid consumer fraud investigations"]
      },
      "strengths": ["Intriguing masculine aesthetic and strong lifestyle hook", "Low product cost with high potential margins"],
      "risks": ["High legal risk regarding misleading health claims under EU consumer protection directives", "Cultural skepticism toward crystal healing in Northern Europe"],
      "required_changes": ["Remove words like 'No pills' which imply medical alternative", "Rewrite text to focus on styling, natural minerals, and active lifestyle rather than masculine drive recovery"],
      "reasoning": "While the jewelry itself is cheap and highly margins-friendly, Europe's consumer regulatory framework is highly hostile to metaphysical or medicinal energy claims. Germany in particular prohibits marketing stones as having biological effects. Must be adapted to fashion jewelry before entering European markets.",
      "strengths_zh": ["极具吸引力的硬朗男性美学设计和强烈的日常痛点切入点", "采购成本极低，具备高溢价空间"],
      "risks_zh": ["在欧盟消费者保护法规下，虚假能量宣传面临极高法律诉讼风险", "北欧国家对水晶疗法的文化信任度较低，可能带来高退货率"],
      "required_changes_zh": ["删除暗示替代药物治疗的敏感字眼（如“无需服药”）", "将营销重点转为天然矿石饰品搭配、硬汉风格等时尚属性，而非生理功效"],
      "reasoning_zh": "尽管该饰品采购成本低且毛利丰厚，但欧洲的消费者监管框架对虚假的能量和疗效宣传非常严苛，尤其是德国禁止将天然矿石作为具有生理调节功能的器具来宣传。进入欧洲前，必须将其降级改造成纯时尚配饰广告。"
    }
  },
  44215: {
    "us": {
      "overall_score": 86,
      "copyability_score": 70,
      "meta_us_ad_fit_score": 88,
      "product_fit_score": 95,
      "compliance_risk_score": 75,
      "recommendation": "adapt",
      "summary": "Highly effective lifestyle food & beverage creative. Features aesthetic morning routine footage and clear benefit callouts. The wellness benefits ('immune support', 'gut health') must be legally backed or softened for standard dropshippers to avoid policy triggers.",
      "summary_zh": "高水平的健康生活方式广告。通过美观的清晨仪式感画面和突出的功效图示引导消费。普通独立站卖家如需跟卖此模式，应注意弱化“免疫支持”、“改善肠胃”等健康宣称以规避Meta风控。",
      "winning_angles": ["Aesthetic morning routine hook", "Coffee alternative positioning (no jitters, crash-free energy)"],
      "copy_notes": ["Use clean kitchen backdrops and satisfying pouring/stirring sound effects (ASMR)", "Clearly display organic certificates and ingredient breakdowns"],
      "risk_notes": ["FDA disclaimer requirement for functional food claims in the US", "Highly competitive market dominated by big players like RYZE and Four Sigmatic"]
    },
    "eu": {
      "suitability_score": 80,
      "recommendation": "adapt_before_translation",
      "direct_reuse": False,
      "translation_fit_score": 82,
      "best_countries": ["Germany", "France", "Italy", "Spain"],
      "country_scores": {"GERMANY": 84, "FRANCE": 80, "ITALY": 76, "SPAIN": 75},
      "source_language_detected": "English",
      "speech_dependency": "high",
      "on_screen_text_dependency": "high",
      "needs_subtitle_translation": True,
      "needs_voiceover_or_dubbing": True,
      "needs_screen_text_replacement": True,
      "localization_difficulty": "high",
      "country_localization_notes": {
        "Germany": ["Strictest EFSA rules on health claims for adaptogenic mushrooms. Need to display German organic certifications (Bio-Siegel) and full ingredient lists in German."],
        "France": ["Must comply with local French food labeling regulations and organic validation (AB cert)."]
      },
      "strengths": ["High-quality visual production showing appealing product texture", "Matches the premium health-conscious demographic in Western Europe"],
      "risks": ["Strict EFSA health claims regulations make functional descriptions highly regulated", "Importation and customs clearance restrictions for agricultural products like mushrooms in the EU"],
      "required_changes": ["Remove unapproved health claims (like specific therapeutic immune benefits)", "Add local language subtitles and translate 'functional ingredients' breakdown into target languages", "Apply for or present equivalent European BIO certificates in creative text"],
      "reasoning": "Functional foods have excellent market potential in Germany and France due to organic wellness trends. However, EFSA regulations on 'adaptogens' and health claims are extremely strict compared to the US FDA. The ad must be localized carefully, toning down health claims to standard lifestyle/wellness language.",
      "strengths_zh": ["高质量的视觉制作，展示了令人愉悦的产品质感与口感", "契合西欧中产阶级对高端、天然有机健康食品的消费升级潮流"],
      "risks_zh": ["欧洲食品安全局(EFSA)对功能性食品的“健康声称”审查极严", "菌菇类农副产品进口至欧盟面临严苛的海关检疫和准入限制"],
      "required_changes_zh": ["剔除未经EFSA审批的具体健康宣称（如特定免疫功能改善）", "翻译功能性原料拆解图，添加德/法文等多语种本地化配音与屏显字幕", "在宣传中展示符合欧洲BIO标准的有机认证"],
      "reasoning_zh": "功能性饮品在德法等欧洲国家拥有极高的溢价空间和受众基础。但是，欧洲食品安全局（EFSA）对“适应原菌菇”功效宣称的监管远比美国FDA严格。广告本地化时，必须将疗效类宣称淡化为偏向生活方式和日常精力补充的温和描述。"
    }
  },
  44205: {
    "us": {
      "overall_score": 65,
      "copyability_score": 20,
      "meta_us_ad_fit_score": 85,
      "product_fit_score": 98,
      "compliance_risk_score": 10,
      "recommendation": "avoid",
      "summary": "Brilliant premium visual creative celebrating Grey Goose. However, because it is a replica of a major intellectual property and alcohol product, it is completely uncopyable and carries maximum legal and policy risk for independent e-commerce sellers.",
      "summary_zh": "视觉效果精美的高端洋酒广告，致敬灰雁伏特加30周年。但由于涉及重大知名品牌侵权以及酒精饮料销售这一高度受限的品类，对独立站卖家而言属于最高风险级别，必须绝对避免。",
      "winning_angles": ["Elegant dark-room ambient lighting", "Physical design and collectible packaging appeal"],
      "copy_notes": ["Avoid replicating the brand. Use the elegant glass sculpting concept for safe home decor products like bottle lights or non-restricted liquids"],
      "risk_notes": ["Severe IP infringement risk (Grey Goose)", "Alcohol sales on Meta are heavily restricted or outright banned for independent sellers without liquor licenses"]
    },
    "eu": {
      "suitability_score": 50,
      "recommendation": "not_recommended",
      "direct_reuse": False,
      "translation_fit_score": 60,
      "best_countries": ["Italy", "Germany", "Spain", "France"],
      "country_scores": {"GERMANY": 55, "FRANCE": 30, "ITALY": 58, "SPAIN": 56},
      "source_language_detected": "English",
      "speech_dependency": "low",
      "on_screen_text_dependency": "medium",
      "needs_subtitle_translation": True,
      "needs_voiceover_or_dubbing": False,
      "needs_screen_text_replacement": True,
      "localization_difficulty": "high",
      "country_localization_notes": {
        "France": ["Loi Evin strictly bans luxury/lifestyle alcohol ads online. Alcohol ads can only show factual product details. Direct compliance failure."],
        "Germany": ["Requires strict age verification and warning statements in the localized copy."]
      },
      "strengths": ["Stunning premium aesthetics showing elegant glass craftsmanship"],
      "risks": ["Total legal barrier for alcohol marketing online in France (Loi Evin)", "High trademark infringement risk on Grey Goose brand"],
      "required_changes": ["Change product to non-restricted fluid or home lighting sculpture", "Add prominent age warning labels (e.g. 18+) as required by European laws"],
      "reasoning": "Selling and advertising branded premium alcohol online is a compliance nightmare in Europe. Specifically, France's Loi Evin restricts alcohol ads strictly to factual parameters (no luxury/lifestyle vibes). Coupled with the Grey Goose trademark violation, this is not recommended.",
      "strengths_zh": ["极佳的奢华灯光质感，将玻璃雕刻工艺展现得淋漓尽致"],
      "risks_zh": ["法国Loi Evin法案对酒类广告的严格限制（禁止灌输奢华和生活方式概念）", "灰雁（Grey Goose）知名商标侵权带来的高昂诉讼与封店处罚风险"],
      "required_changes_zh": ["将产品品类更换为不受限的无酒精装饰摆件或发光玻璃瓶", "添加欧洲各国法定的饮酒年龄限制警示（如18+）以及理性饮酒标语"],
      "reasoning_zh": "在欧洲，线上推广品牌酒类面临极严苛的合规壁垒。法国的Loi Evin法案强制规定酒类广告只能进行纯客观的事实陈述，禁止任何虚荣和享受导向的场景。加上产品本身对知名伏特加品牌的侵权，强烈建议不予跟卖推广。"
    }
  },
  44204: {
    "us": {
      "overall_score": 92,
      "copyability_score": 95,
      "meta_us_ad_fit_score": 95,
      "product_fit_score": 96,
      "compliance_risk_score": 98,
      "recommendation": "copy",
      "summary": "An outstanding pet-niche creative. Features the classic 'satisfying deshedding' hook, pulling off a huge clump of hair in the first 3 seconds. Extremely high conversion potential and very low replication cost.",
      "summary_zh": "宠物赛道的绝佳爆款广告。前3秒展示了“极度舒适”的梳毛和撕下整片毛发的解压画面，极易吸引铲屎官停留。转化率潜力大，复刻成本极低。",
      "winning_angles": ["Satisfying deshedding hook (peeling away the hair mat)", "Gentle rounded blade safety demonstration"],
      "copy_notes": ["Ensure the opening scene features a highly satisfying 'peel-off' of the collected hair", "Show pet's relaxed and happy reaction during grooming to emphasize safety"],
      "risk_notes": ["Highly saturated market, need distinct branding or bundle offers to stand out"]
    },
    "eu": {
      "suitability_score": 92,
      "recommendation": "translate_and_launch",
      "direct_reuse": False,
      "translation_fit_score": 92,
      "best_countries": ["Germany", "France", "Italy", "Spain"],
      "country_scores": {"GERMANY": 94, "FRANCE": 92, "ITALY": 90, "SPAIN": 89},
      "source_language_detected": "English",
      "speech_dependency": "medium",
      "on_screen_text_dependency": "medium",
      "needs_subtitle_translation": True,
      "needs_voiceover_or_dubbing": True,
      "needs_screen_text_replacement": True,
      "localization_difficulty": "low",
      "country_localization_notes": {
        "Germany": ["High standard of animal welfare. Highlight the rounded scratch-free safety of the stainless steel blades (Sicherheit / Fellschonend)."],
        "France": ["Focus on home hygiene (less hair on sofa/rugs) and comfort for the pet."]
      },
      "strengths": ["Universally appealing satisfying grooming visual hooks", "High-margin, lightweight product that is cheap to ship to Europe"],
      "risks": ["High brand saturation in the EU Amazon/Shopify pet niches"],
      "required_changes": ["Translate 'Groom Easy. Fur Gone.' to local equivalent (e.g. 'Einfache Fellpflege. Lose Haare weg!' for Germany)", "Use warm, friendly local voiceovers that highlight safety and domestic cleanliness"],
      "reasoning": "Pets are viewed as close family members in Germany and France, making animal welfare and comfort critical selling points. The grooming tool's visual hook of satisfyingly removing massive undercoats is globally effective. Fast, easy localization of overlays and a gentle localized voiceover will enable quick launch.",
      "strengths_zh": ["全球通用的“梳毛解压”视觉钩子，跨越文化与语言壁垒", "高毛利、轻量化，极其契合欧洲的小包直邮物流要求"],
      "risks_zh": ["欧洲主流电商平台（Amazon、Shopify）在宠物梳类目已有较高饱和度"],
      "required_changes_zh": ["将核心文案翻译为本地语种（如德语：'Einfache Fellpflege. Lose Haare weg!'）", "配上亲切温和的本地化配音，强调对宠物的安全呵护以及居室清洁效果"],
      "reasoning_zh": "在德法等国，宠物被视为重要的家庭成员，因此铲屎官对宠物用品的安全性与舒适度高度关注。该产品通过轻松带走厚重浮毛的视觉呈现，具有天然的吸睛力。只需翻译屏显文字并增加贴近当地口吻的温柔配音，即可在欧洲快速起量。"
    }
  },
  44164: {
    "us": {
      "overall_score": 74,
      "copyability_score": 82,
      "meta_us_ad_fit_score": 78,
      "product_fit_score": 90,
      "compliance_risk_score": 95,
      "recommendation": "adapt",
      "summary": "A simple 'packing order' style ad showing customized high-protein oats packets being placed in a branded box. Safe and easy to reproduce, but the engagement on this specific post is extremely low, suggesting it is a retargeting or organic test creative. Better as part of an organic TikTok strategy.",
      "summary_zh": "简单低成本的“打包订单”Vlog类广告，展示了定制高蛋白燕麦袋被放入精装盒的过程。易于复刻，但该帖的自然互动极低，属于定向重营销或自然流量测试创意，可配合社媒账号矩阵使用。",
      "winning_angles": ["ASMR order packing format", "Customized box set value presentation"],
      "copy_notes": ["Focus on ASMR sound effects of box folding and packing", "Highlight the convenience and cost-per-meal savings in the text overlay"],
      "risk_notes": ["Extremely low engagement, not a proven standalone winning ad", "High operational complexity for custom subscription models in standard dropshipping"]
    },
    "eu": {
      "suitability_score": 72,
      "recommendation": "adapt_before_translation",
      "direct_reuse": False,
      "translation_fit_score": 78,
      "best_countries": ["Germany", "France", "Italy", "Spain"],
      "country_scores": {"GERMANY": 78, "FRANCE": 72, "ITALY": 68, "SPAIN": 65},
      "source_language_detected": "English",
      "speech_dependency": "low",
      "on_screen_text_dependency": "medium",
      "needs_subtitle_translation": True,
      "needs_voiceover_or_dubbing": True,
      "needs_screen_text_replacement": True,
      "localization_difficulty": "medium",
      "country_localization_notes": {
        "Germany": ["High requirement for organic/clean ingredients listing in German.", "Need to comply with local food packaging weight disclosures."],
        "France": ["Requires Nutri-Score classification (high protein oats usually rank high, A or B, which is a major sales booster)."]
      },
      "strengths": ["Genuine organic ASMR vibe that builds trust with consumers", "Promotes high-protein healthy breakfast trend"],
      "risks": ["High shipping costs for food boxes to Europe", "Customs declaration issues for dairy/soy proteins from non-EU origins"],
      "required_changes": ["Translate on-screen packing text into local languages", "Include European nutritional grading systems (e.g. Nutri-Score) to build rapid trust"],
      "reasoning": "The 'pack an order with me' format is globally recognized and highly effective for trust-building. High-protein oats are popular in Germany, but food subscription relies heavily on local shipping. The marketing strategy should pivot to single-purchase trial packs and localized organic labels to clear regulatory and logistics hurdles.",
      "strengths_zh": ["真实自然的打包ASMR氛围，能有效建立用户信任度", "顺应了欧洲年轻人高蛋白、快节奏健康早餐的潮流"],
      "risks_zh": ["食品箱重且直邮运费高昂", "非欧盟产地的乳清或植物蛋白粉进口可能遭遇严格的准入检疫"],
      "required_changes_zh": ["将打包过程中的闲聊或屏幕解说文字翻译为当地语言", "在视频或详情页中嵌入欧洲本地的Nutri-Score（营养评分）等级以增加公信力"],
      "reasoning_zh": "“陪我一起打包订单”的视频形式在TikTok上全球通用，非常适合建立品牌真实感。高蛋白燕麦在德国等地很有潜力，但食品订阅模式严重依赖本地物流。建议针对欧洲将订阅制弱化为“尝鲜试吃装”，并提供符合当地海关要求的纯中文/英文标签转换。"
    }
  },
  44163: {
    "us": {
      "overall_score": 68,
      "copyability_score": 10,
      "meta_us_ad_fit_score": 85,
      "product_fit_score": 90,
      "compliance_risk_score": 98,
      "recommendation": "avoid",
      "summary": "ALDI brand campaign emphasizing efficiency and cost savings ('no baggers = cheaper prices'). High shareability due to cultural resonance, but completely useless for standard e-commerce dropshippers as it is a major retail brand promotion.",
      "summary_zh": "阿路迪（ALDI）的品牌广告，强调通过“自助装袋”节省人力，进而压低商品售价。因引发消费者共鸣而获得极高转发，但对于跨境电商卖家而言完全无法跟卖复制。",
      "winning_angles": ["Transparency in cost-cutting", "Humorous self-deprecating culture callouts"],
      "copy_notes": ["Adopt the business philosophy of 'explaining why we are cheap' (e.g. factory direct, no middleman) in your own dropshipping copy"],
      "risk_notes": ["Brand advertising for brick-and-mortar retail has zero dropshipping utility"]
    },
    "eu": {
      "suitability_score": 95,
      "recommendation": "not_recommended",
      "direct_reuse": False,
      "translation_fit_score": 80,
      "best_countries": ["Germany", "United Kingdom", "France", "Italy"],
      "country_scores": {"GERMANY": 98, "FRANCE": 90, "ITALY": 85, "SPAIN": 82},
      "source_language_detected": "English",
      "speech_dependency": "low",
      "on_screen_text_dependency": "medium",
      "needs_subtitle_translation": True,
      "needs_voiceover_or_dubbing": False,
      "needs_screen_text_replacement": True,
      "localization_difficulty": "low",
      "country_localization_notes": {
        "Germany": ["ALDI's home turf. No translation needed if targeting UK/US, but German audience already does this daily as a cultural norm."],
        "France": ["Must frame bagging culture in the context of the cost-of-living crisis (pouvoir d'achat)."]
      },
      "strengths": ["Deep cultural resonance with European thriftiness and self-service standards"],
      "risks": ["Brand campaign for offline retail, not viable for online e-commerce export"],
      "required_changes": ["None, product and business model are not adaptable to e-commerce"],
      "reasoning": "ALDI is a European discount supermarket giant. The core value of self-service and low costs is deeply rooted in European culture. However, this is an offline retail campaign that holds no practical value for online export/cross-border dropshippers.",
      "strengths_zh": ["极其契合欧洲人崇尚务实、自助以及节俭的社会文化"],
      "risks_zh": ["这是纯实体连锁零售商的本地品牌宣贯，不具备任何线上跨境出口可行性"],
      "required_changes_zh": ["无，商业模式完全无法线上化适配"],
      "reasoning_zh": "ALDI（奥乐齐）是源自德国的欧洲折扣超市巨头。该广告所宣传的“不设装袋员以换取更低售价”在欧洲本地属于常识，具有极强的人文共鸣。但它仅服务于实体商超引流，跨境电商卖家无法借鉴其实际业务。"
    }
  },
  44162: {
    "us": {
      "overall_score": 82,
      "copyability_score": 65,
      "meta_us_ad_fit_score": 85,
      "product_fit_score": 92,
      "compliance_risk_score": 90,
      "recommendation": "adapt",
      "summary": "Superb problem-solving copywriting targeting pet hygiene ('No more huge, smelly poops!'). The business model requires heavy cold-chain shipping, making it uncopyable for standard dropshippers, but the hook and benefits are prime targets for premium pet food brands.",
      "summary_zh": "非常出色的痛点营销文案，直击铲屎官痛点（“告别又大又臭的便便！”）。由于生鲜犬粮需要冷链物流，普通无源卖家极难直接跟卖，但其宣传语境和排毒痛点极具启发意义。",
      "winning_angles": ["Hygiene pain point (small poops, less smell)", "Raw diet digestion improvements"],
      "copy_notes": ["Use bold emojis and text overlay focusing on daily maintenance convenience", "Show high-quality close-ups of natural raw meat cuts"],
      "risk_notes": ["Raw meat logistics requires specialized cold chain shipping which is highly expensive and complex", "Low engagement indicates a fresh creative test or retargeting copy"]
    },
    "eu": {
      "suitability_score": 78,
      "recommendation": "adapt_before_translation",
      "direct_reuse": False,
      "translation_fit_score": 80,
      "best_countries": ["Germany", "United Kingdom", "France", "Italy"],
      "country_scores": {"GERMANY": 86, "FRANCE": 78, "ITALY": 70, "SPAIN": 68},
      "source_language_detected": "English",
      "speech_dependency": "medium",
      "on_screen_text_dependency": "high",
      "needs_subtitle_translation": True,
      "needs_voiceover_or_dubbing": True,
      "needs_screen_text_replacement": True,
      "localization_difficulty": "high",
      "country_localization_notes": {
        "Germany": ["BARF culture is highly developed. Need to use local terminology 'BARF' (Biologisch Artgerechtes Rohes Futter). Strict veterinary import guidelines apply."],
        "France": ["Must focus on premium ingredients and organic origin to appeal to French pet owners."]
      },
      "strengths": ["Taps into a massive European pet wellness trend (BARF diet)", "Copy points (smaller poops) are universally persuasive"],
      "risks": ["Severe import and shipping barriers for raw/frozen animal products across EU borders", "Stricter food safety regulations on raw meat feeding due to Salmonella concerns"],
      "required_changes": ["Translate all bullet points, adapting 'raw' to 'BARF' for the German audience", "Tone down overly aggressive health disclaimers to comply with EU feed regulations (EC 767/2009)"],
      "reasoning": "German pet owners are deeply obsessed with high-quality dog nutrition and BARF diets, meaning the market is prime. However, raw frozen logistics from outside Europe is virtually impossible for cross-border sellers due to sanitary certificates and strict cold-chain requirements. The marketing must be adapted for dehydrated raw or freeze-dried pet foods, which are dry-shipped easily.",
      "strengths_zh": ["完美切入欧洲极其庞大的生食喂养（BARF）宠物健康潮流", "直击“便便体积变小、异味减轻”的痛点，说服力跨越国界"],
      "risks_zh": ["冷链生鲜肉制品跨国进出口面临极度严苛的欧盟动植物检疫壁垒", "生肉喂养在欧洲由于沙门氏菌等菌群控制标准，面临较多食品安全争议"],
      "required_changes_zh": ["将所有宣传要点翻译为当地语言，在德国需使用本土化词汇“BARF”替代“Raw”", "若要跟卖，必须将产品调整为“冻干生肉”或“脱水生粮”等常温易物流的形态，并重新拍摄常温包装视频"],
      "reasoning_zh": "德国宠物主极其推崇“生食喂养（BARF）”，市场接受度极高。然而，冷链生鲜食品直邮欧洲在海关检疫和冷链运输上成本高昂，普通跨境电商难以承载。建议将其改造成“常温冻干生肉主粮”，在保留高营养心智的同时彻底解决国际物流难题。"
    }
  },
  44161: {
    "us": {
      "overall_score": 72,
      "copyability_score": 60,
      "meta_us_ad_fit_score": 75,
      "product_fit_score": 95,
      "compliance_risk_score": 90,
      "recommendation": "adapt",
      "summary": "Clean, simple presentation of fermented gut-health food (pickles, kraut). Focuses on probiotic benefits and satisfying crunch. Low engagement suggests a fresh or low-spend local ad. Shipping liquid-filled glass jars fresh makes it very difficult for standard e-commerce dropshippers without local warehousing.",
      "summary_zh": "展示发酵益生菌食品（泡菜、酸菜、橄榄）的干净广告，主打肠道健康和爽脆口感。互动量接近零，属于刚上线的测试创意。由于泡菜含液体且需保鲜，普通直邮独立站极难解决包装和破损问题，需要海外仓发货。",
      "winning_angles": ["ASMR crunch sound hook", "Zero sugar, zero vinegar, live probiotic health angle"],
      "copy_notes": ["Include loud, satisfying chewing ASMR sounds in the first 2 seconds", "Show the natural fermentation process to emphasize authenticity"],
      "risk_notes": ["Extremely difficult shipping profile (liquid in glass jars, fresh/fermented state)", "Zero validated engagement on this specific ad creative"]
    },
    "eu": {
      "suitability_score": 80,
      "recommendation": "adapt_before_translation",
      "direct_reuse": False,
      "translation_fit_score": 78,
      "best_countries": ["Germany", "France", "United Kingdom", "Poland"],
      "country_scores": {"GERMANY": 82, "FRANCE": 75, "ITALY": 70, "SPAIN": 72},
      "source_language_detected": "English",
      "speech_dependency": "low",
      "on_screen_text_dependency": "medium",
      "needs_subtitle_translation": True,
      "needs_voiceover_or_dubbing": True,
      "needs_screen_text_replacement": True,
      "localization_difficulty": "high",
      "country_localization_notes": {
        "Germany": ["Germany is the homeland of Sauerkraut. Expect extreme skepticism if importing standard kraut. Must focus on premium artisanal flavors (e.g. gourmet styling)."],
        "France": ["Requires localized labels in French with full organic classification if marketed as bio."]
      },
      "strengths": ["Appeals to the strong European interest in gut health and microbiome support", "Strong ASMR eating audio has universal appeal"],
      "risks": ["Mature and very cheap local European market for pickles and sauerkraut", "Glass packaging and liquid transport regulations across international borders"],
      "required_changes": ["Rewrite copy to highlight premium artisan quality rather than basic kraut/pickles to bypass local cheap alternatives", "Translate health benefit callouts (like 'billions of live probiotics') into target languages"],
      "reasoning": "Fermented foods like Sauerkraut and pickles are already incredibly cheap and high quality in Germany and Eastern Europe. Selling them online from an overseas brand requires positioning as a luxury, high-end organic health delicacy. The liquid and glass shipping logistics present a major barrier, making it vital to adapt the packaging to vacuum-sealed dry-packs or powdered probiotic foods.",
      "strengths_zh": ["切中欧洲人特别是中产对肠道菌群与微生态健康的强烈关注", "爽脆的吃播咀嚼音（ASMR）具有天然的跨语种诱惑力"],
      "risks_zh": ["欧洲本地（如德、波）拥有极为成熟、价格极其低廉的泡菜与酸菜市场", "玻璃罐装液体食品在国际长途运输中破损率高、运费昂贵"],
      "required_changes_zh": ["将营销话术从普通家常泡菜升级为“高端精酿发酵膳食”，避开与当地廉价商超产品的价格战", "将“数十亿活性益生菌”等功效词汇翻译为对应语种，并在标签中显示欧洲本地的有机标准"],
      "reasoning_zh": "酸菜（Sauerkraut）和酸黄瓜本身就是德国等中欧国家的国民级平价食品。如果跨境电商想向欧洲卖泡菜，必须走高端手作、特色口味（如亚洲风味、精酿益生菌）路线。物流方面，玻璃罐加液体的直邮成本是灾难性的，测试前必须改用真空轻量软包装或将其改造为便携干粉制剂。"
    }
  }
}

def execute_backfill(post_id: int, us_data: dict, eu_data: dict) -> None:
    print(f"[*] Processing Post {post_id}...")

    # 1. US Copyability
    store.ensure_video_copyability_candidate_for_post(post_id)
    us_state = store.get_video_copyability_analysis_state(post_id)
    if not us_state:
        raise RuntimeError(f"Could not prepare US candidate for post {post_id}")
    
    analysis_id = int(us_state["id"])
    store.mark_video_copyability_running(analysis_id)
    
    us_result = {
        "overall_score": us_data.get("overall_score", 0),
        "copyability_score": us_data.get("copyability_score", 0),
        "meta_us_ad_fit_score": us_data.get("meta_us_ad_fit_score", 0),
        "product_fit_score": us_data.get("product_fit_score", 0),
        "compliance_risk_score": us_data.get("compliance_risk_score", 0),
        "recommendation": us_data.get("recommendation", "adapt"),
        "summary": us_data.get("summary", ""),
        "summary_zh": us_data.get("summary_zh", ""),
        "winning_angles": us_data.get("winning_angles") or [],
        "copy_notes": us_data.get("copy_notes") or [],
        "risk_notes": us_data.get("risk_notes") or [],
        "provider": "antigravity",
        "model": "gemini-3.5-flash"
    }
    
    affected_us = store.finish_video_copyability_analysis(analysis_id, result=us_result)
    print(f"  [+] US backfilled. Affected: {affected_us}")

    # 2. Europe Fit & Translation
    store.ensure_europe_fit_candidate_for_post(post_id)
    store.mark_europe_fit_running(post_id)
    
    eu_result = {
        "suitability_score": eu_data.get("suitability_score", 0),
        "recommendation": eu_data.get("recommendation", "adapt_before_translation"),
        "direct_reuse": bool(eu_data.get("direct_reuse")),
        "translation_fit_score": eu_data.get("translation_fit_score", 0),
        "best_countries": eu_data.get("best_countries") or [],
        "country_scores": eu_data.get("country_scores") or {},
        "strengths": eu_data.get("strengths") or [],
        "risks": eu_data.get("risks") or [],
        "required_changes": eu_data.get("required_changes") or [],
        "reasoning": eu_data.get("reasoning", ""),
        "provider": "antigravity",
        "model": "gemini-3.5-flash",
        "raw_response": eu_data
    }
    
    eu_translation = {
        "strengths": eu_data.get("strengths_zh") or eu_data.get("strengths") or [],
        "risks": eu_data.get("risks_zh") or eu_data.get("risks") or [],
        "required_changes": eu_data.get("required_changes_zh") or eu_data.get("required_changes") or [],
        "reasoning": eu_data.get("reasoning_zh") or eu_data.get("reasoning") or ""
    }
    
    affected_eu_fit = store.finish_europe_fit_assessment(post_id, status="done", result=eu_result)
    affected_eu_zh = store.finish_europe_fit_translation(post_id, translated=eu_translation, error_message=None)
    print(f"  [+] Europe backfilled. Fit affected: {affected_eu_fit}, Zh affected: {affected_eu_zh}")

def main():
    print("=== Starting Antigravity Batch Serial Backfill ===")
    success_count = 0
    total = len(EVALUATIONS)
    
    for post_id, data in EVALUATIONS.items():
        try:
            execute_backfill(post_id, data["us"], data["eu"])
            success_count += 1
        except Exception as e:
            print(f"  [!] Failed to backfill post {post_id}: {e}", file=sys.stderr)
            
    print(f"\n=== Batch Complete. Success: {success_count}/{total} ===")
    if success_count == total:
        print("[*] All evaluations successfully saved to production database.")
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
