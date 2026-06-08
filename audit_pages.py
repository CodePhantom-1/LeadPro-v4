"""
LeadPro v3 — Personalized Audit Landing Pages
Generates a unique shareable HTML audit page per lead.
"""
import os
import hashlib
import json
import html
import secrets
import urllib.parse
import datetime
from config import YOUR_EMAIL, YOUR_COMPANY, BASE_URL, AUDIT_PAGE_EXPIRY_HOURS, SOCIAL_PROOF_TEXT, NICHE_SOCIAL_PROOF
from database import get_conn
from audit import get_currency_for_country, get_language_for_country


UI_TRANSLATIONS = {
    "en": {
        "audit_title": "Digital Presence Audit", "prepared_for": "Prepared for",
        "revenue_left": "Estimated Revenue Left on the Table", "per_month": "/mo",
        "per_year": "/year", "thats": "That's", "revenue_impact": "Revenue Impact",
        "total_monthly": "Total Monthly", "tech_stack": "Technology Stack",
        "not_detected": "Not detected", "search_visibility": "Search Visibility",
        "not_found": "Not found", "competitors": "Competitor Comparison",
        "health_score": "Digital Health Score",
        "urgent": "Needs urgent attention", "improve": "Room for improvement",
        "decent": "Decent — competitors may be ahead",
        "full_report": "Get the Full Report",
        "enter_email": "Enter your email to receive the detailed audit PDF and recommendations.",
        "email_placeholder": "your@business.com", "send_report": "Send Report",
        "book_call": "Book a Free Strategy Call",
        "expires_in": "Expires in {} hours", "expired": "Expired",
        "generated": "Generated from publicly available data", "views": "views",
        "monthly_loss_chart": "Monthly Loss", "annual_impact": "Annual Impact",
        "issue": "Issue", "monthly_cost": "Monthly Cost",
        "position": "Position", "keyword": "Keyword",
        "your_business": "Your Business",
        "score_breakdown": "Score Breakdown", "website": "Website",
        "mobile": "Mobile", "tracking": "Tracking", "social": "Social",
        "speed": "Speed", "seo_positions": "SEO Positions",
        "privacy_note": "This audit was generated from publicly available data. No private information was accessed.",
    },
    "de": {
        "audit_title": "Digitales Präsenz-Audit", "prepared_for": "Erstellt für",
        "revenue_left": "Geschätzter Umsatzverlust", "per_month": "/Mo.",
        "per_year": "/Jahr", "thats": "Das sind", "revenue_impact": "Umsatzauswirkung",
        "total_monthly": "Monatlich gesamt", "tech_stack": "Technologie-Stack",
        "not_detected": "Nicht erkannt", "search_visibility": "Suchsichtbarkeit",
        "not_found": "Nicht gefunden", "competitors": "Wettbewerber-Vergleich",
        "health_score": "Digitaler Gesundheits-Score",
        "urgent": "Dringender Handlungsbedarf", "improve": "Verbesserungspotenzial",
        "decent": "Akzeptabel — Wettbewerber könnten voraus sein",
        "full_report": "Vollständigen Bericht anfordern",
        "enter_email": "Geben Sie Ihre E-Mail ein, um das detaillierte Audit-PDF zu erhalten.",
        "email_placeholder": "ihre@firma.de", "send_report": "Bericht senden",
        "book_call": "Kostenlosen Beratungstermin buchen",
        "expires_in": "Läuft ab in {} Stunden", "expired": "Abgelaufen",
        "generated": "Erstellt aus öffentlich verfügbaren Daten", "views": "Aufrufe",
        "monthly_loss_chart": "Monatlicher Verlust", "annual_impact": "Jährliche Auswirkung",
        "issue": "Problem", "monthly_cost": "Monatliche Kosten",
        "position": "Position", "keyword": "Suchbegriff",
        "your_business": "Ihr Unternehmen",
        "score_breakdown": "Score-Aufschlüsselung", "website": "Website",
        "mobile": "Mobil", "tracking": "Tracking", "social": "Social",
        "speed": "Geschwindigkeit", "seo_positions": "SEO-Positionen",
        "privacy_note": "Dieses Audit wurde aus öffentlich verfügbaren Daten erstellt.",
    },
    "fr": {
        "audit_title": "Audit de Présence Digitale", "prepared_for": "Préparé pour",
        "revenue_left": "Revenus estimés non captés", "per_month": "/mois",
        "per_year": "/an", "thats": "C'est", "revenue_impact": "Impact sur les revenus",
        "total_monthly": "Total mensuel", "tech_stack": "Stack Technologique",
        "not_detected": "Non détecté", "search_visibility": "Visibilité de recherche",
        "not_found": "Non trouvé", "competitors": "Comparaison Concurrents",
        "health_score": "Score de Santé Digitale",
        "urgent": "Attention urgente nécessaire", "improve": "Marge d'amélioration",
        "decent": "Correct — les concurrents pourraient être en avance",
        "full_report": "Recevoir le rapport complet",
        "enter_email": "Entrez votre email pour recevoir le PDF d'audit détaillé.",
        "email_placeholder": "votre@entreprise.fr", "send_report": "Envoyer le rapport",
        "book_call": "Réservez un appel stratégique gratuit",
        "expires_in": "Expire dans {} heures", "expired": "Expiré",
        "generated": "Généré à partir de données publiques", "views": "vues",
        "monthly_loss_chart": "Perte mensuelle", "annual_impact": "Impact annuel",
        "issue": "Problème", "monthly_cost": "Coût mensuel",
        "position": "Position", "keyword": "Mot-clé",
        "your_business": "Votre entreprise",
        "score_breakdown": "Détail du score", "website": "Site web",
        "mobile": "Mobile", "tracking": "Tracking", "social": "Social",
        "speed": "Vitesse", "seo_positions": "Positions SEO",
        "privacy_note": "Cet audit a été généré à partir de données publiques.",
    },
    "es": {
        "audit_title": "Auditoría de Presencia Digital", "prepared_for": "Preparado para",
        "revenue_left": "Ingresos estimados no captados", "per_month": "/mes",
        "per_year": "/año", "thats": "Eso es", "revenue_impact": "Impacto en Ingresos",
        "total_monthly": "Total mensual", "tech_stack": "Stack Tecnológico",
        "not_detected": "No detectado", "search_visibility": "Visibilidad de búsqueda",
        "not_found": "No encontrado", "competitors": "Comparativa de Competidores",
        "health_score": "Puntuación de Salud Digital",
        "urgent": "Necesita atención urgente", "improve": "Margen de mejora",
        "decent": "Aceptable — los competidores pueden estar por delante",
        "full_report": "Obtener el informe completo",
        "enter_email": "Ingrese su email para recibir el PDF de auditoría detallado.",
        "email_placeholder": "su@empresa.es", "send_report": "Enviar informe",
        "book_call": "Reserve una estrategia gratuita",
        "expires_in": "Expira en {} horas", "expired": "Expirado",
        "generated": "Generado a partir de datos públicos", "views": "visitas",
        "monthly_loss_chart": "Pérdida mensual", "annual_impact": "Impacto anual",
        "issue": "Problema", "monthly_cost": "Costo mensual",
        "position": "Posición", "keyword": "Palabra clave",
        "your_business": "Su negocio",
        "score_breakdown": "Desglose del score", "website": "Sitio web",
        "mobile": "Móvil", "tracking": "Seguimiento", "social": "Social",
        "speed": "Velocidad", "seo_positions": "Posiciones SEO",
        "privacy_note": "Esta auditoría se generó a partir de datos públicos.",
    },
    "pt": {
        "audit_title": "Auditoria de Presença Digital", "prepared_for": "Preparado para",
        "revenue_left": "Receita estimada não capturada", "per_month": "/mês",
        "per_year": "/ano", "thats": "Isso é", "revenue_impact": "Impacto na Receita",
        "total_monthly": "Total mensal", "tech_stack": "Stack Tecnológica",
        "not_detected": "Não detectado", "search_visibility": "Visibilidade de busca",
        "not_found": "Não encontrado", "competitors": "Comparação de Concorrentes",
        "health_score": "Pontuação de Saúde Digital",
        "urgent": "Necessita atenção urgente", "improve": "Margem de melhoria",
        "decent": "Aceitável — concorrentes podem estar à frente",
        "full_report": "Obter o relatório completo",
        "enter_email": "Insira seu email para receber o PDF de auditoria detalhado.",
        "email_placeholder": "seu@empresa.com.br", "send_report": "Enviar relatório",
        "book_call": "Agende uma chamada estratégica gratuita",
        "expires_in": "Expira em {} horas", "expired": "Expirado",
        "generated": "Gerado a partir de dados públicos", "views": "visualizações",
        "monthly_loss_chart": "Perda mensal", "annual_impact": "Impacto anual",
        "issue": "Problema", "monthly_cost": "Custo mensal",
        "position": "Posição", "keyword": "Palavra-chave",
        "your_business": "Sua empresa",
        "score_breakdown": "Detalhamento do score", "website": "Site",
        "mobile": "Mobile", "tracking": "Rastreamento", "social": "Social",
        "speed": "Velocidade", "seo_positions": "Posições SEO",
        "privacy_note": "Esta auditoria foi gerada a partir de dados públicos.",
    },
    "it": {
        "audit_title": "Audit Presenza Digitale", "prepared_for": "Preparato per",
        "revenue_left": "Ricavi stimati non catturati", "per_month": "/mese",
        "per_year": "/anno", "thats": "Questo equivale a", "revenue_impact": "Impatto sui Ricavi",
        "total_monthly": "Totale mensile", "tech_stack": "Stack Tecnologico",
        "not_detected": "Non rilevato", "search_visibility": "Visibilità di ricerca",
        "not_found": "Non trovato", "competitors": "Confronto Concorrenti",
        "health_score": "Punteggio Salute Digitale",
        "urgent": "Richiede attenzione urgente", "improve": "Margine di miglioramento",
        "decent": "Discreto — i concorrenti potrebbero essere avanti",
        "full_report": "Ottieni il report completo",
        "enter_email": "Inserisci la tua email per ricevere il PDF dell'audit dettagliato.",
        "email_placeholder": "tua@azienda.it", "send_report": "Invia report",
        "book_call": "Prenota una consulenza strategica gratuita",
        "expires_in": "Scade tra {} ore", "expired": "Scaduto",
        "generated": "Generato da dati pubblici", "views": "visualizzazioni",
        "monthly_loss_chart": "Perdita mensile", "annual_impact": "Impatto annuale",
        "issue": "Problema", "monthly_cost": "Costo mensile",
        "position": "Posizione", "keyword": "Parola chiave",
        "your_business": "La tua azienda",
        "score_breakdown": "Dettaglio punteggio", "website": "Sito web",
        "mobile": "Mobile", "tracking": "Tracciamento", "social": "Social",
        "speed": "Velocità", "seo_positions": "Posizioni SEO",
        "privacy_note": "Questo audit è stato generato da dati pubblici.",
    },
    "nl": {
        "audit_title": "Digitale Aanwezigheid Audit", "prepared_for": "Opgesteld voor",
        "revenue_left": "Geschatte gemiste omzet", "per_month": "/mnd",
        "per_year": "/jr", "thats": "Dat is", "revenue_impact": "Omzetimpact",
        "total_monthly": "Totaal maandelijks", "tech_stack": "Technologiestack",
        "not_detected": "Niet gedetecteerd", "search_visibility": "Zoekzichtbaarheid",
        "not_found": "Niet gevonden", "competitors": "Concurrentenvergelijking",
        "health_score": "Digitale Gezondheidsscore",
        "urgent": "Dringende aandacht vereist", "improve": "Ruimte voor verbetering",
        "decent": "Redelijk — concurrenten kunnen voorliggen",
        "full_report": "Volledig rapport ophalen",
        "enter_email": "Voer uw e-mail in om het gedetailleerde audit-PDF te ontvangen.",
        "email_placeholder": "uw@bedrijf.nl", "send_report": "Rapport verzenden",
        "book_call": "Boek een gratis strategisch gesprek",
        "expires_in": "Verloopt over {} uur", "expired": "Verlopen",
        "generated": "Gegenereerd uit openbare gegevens", "views": "weergaven",
        "monthly_loss_chart": "Maandelijks verlies", "annual_impact": "Jaarlijkse impact",
        "issue": "Probleem", "monthly_cost": "Maandelijkse kosten",
        "position": "Positie", "keyword": "Trefwoord",
        "your_business": "Uw bedrijf",
        "score_breakdown": "Score-overzicht", "website": "Website",
        "mobile": "Mobiel", "tracking": "Tracking", "social": "Social",
        "speed": "Snelheid", "seo_positions": "SEO-posities",
        "privacy_note": "Deze audit is gegenereerd uit openbare gegevens.",
    },
    "ja": {
        "audit_title": "デジタルプレゼンス監査レポート", "prepared_for": "作成対象:",
        "revenue_left": "推定機会損失", "per_month": "/月",
        "per_year": "/年", "thats": "", "revenue_impact": "収益への影響",
        "total_monthly": "月合計", "tech_stack": "テクノロジースタック",
        "not_detected": "未検出", "search_visibility": "検索表示",
        "not_found": "未検出", "competitors": "競合他社との比較",
        "health_score": "デジタルヘルススコア",
        "urgent": "緊急の対応が必要", "improve": "改善の余地あり",
        "decent": "まずまず — 競合が先行している可能性",
        "full_report": "完全レポートを取得",
        "enter_email": "メールアドレスを入力して詳細な監査PDFを受け取る。",
        "email_placeholder": "your@business.com", "send_report": "レポート送信",
        "book_call": "無料戦略相談を予約する",
        "expires_in": "{}時間後に期限切れ", "expired": "期限切れ",
        "generated": "公開データから生成", "views": "閲覧数",
        "monthly_loss_chart": "月間損失", "annual_impact": "年間影響",
        "issue": "問題", "monthly_cost": "月間コスト",
        "position": "順位", "keyword": "キーワード",
        "your_business": "あなたのビジネス",
        "score_breakdown": "スコア内訳", "website": "ウェブサイト",
        "mobile": "モバイル", "tracking": "トラッキング", "social": "ソーシャル",
        "speed": "速度", "seo_positions": "SEO順位",
        "privacy_note": "この監査は公開データから生成されました。",
    },
    "ko": {
        "audit_title": "디지털 프레즌스 감사 보고서", "prepared_for": "대상:",
        "revenue_left": "예상 매출 손실", "per_month": "/월",
        "per_year": "/년", "thats": "", "revenue_impact": "매출 영향",
        "total_monthly": "월간 합계", "tech_stack": "기술 스택",
        "not_detected": "미감지", "search_visibility": "검색 가시성",
        "not_found": "미감지", "competitors": "경쟁사 비교",
        "health_score": "디지털 건강 점수",
        "urgent": "긴급 조치 필요", "improve": "개선 여지 있음",
        "decent": "양호 — 경쟁사가 앞설 수 있음",
        "full_report": "전체 보고서 받기",
        "enter_email": "이메일을 입력하여 상세 감사 PDF를 받으세요.",
        "email_placeholder": "your@business.com", "send_report": "보고서 전송",
        "book_call": "무료 전략 상담 예약하기",
        "expires_in": "{}시간 후 만료", "expired": "만료됨",
        "generated": "공개 데이터에서 생성", "views": "조회수",
        "monthly_loss_chart": "월간 손실", "annual_impact": "연간 영향",
        "issue": "문제", "monthly_cost": "월간 비용",
        "position": "순위", "keyword": "키워드",
        "your_business": "귀하의 비즈니스",
        "score_breakdown": "점수 세분화", "website": "웹사이트",
        "mobile": "모바일", "tracking": "추적", "social": "소셜",
        "speed": "속도", "seo_positions": "SEO 순위",
        "privacy_note": "이 감사는 공개 데이터에서 생성되었습니다.",
    },
}


def _t(key: str, lang: str) -> str:
    return UI_TRANSLATIONS.get(lang, UI_TRANSLATIONS["en"]).get(key, UI_TRANSLATIONS["en"].get(key, key))


_ICONS = {
    "sun": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>',
    "monitor": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>',
    "search": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>',
    "users": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>',
    "pulse": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>',
    "mail": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M22 7l-10 6L2 7"/></svg>',
    "clock": '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
}


def _card(icon_key, color, title, body_html):
    return (
        '<div class="card"><div class="card-header">'
        f'<div class="card-icon {color}">{_ICONS[icon_key]}</div>'
        f'<div class="card-title">{title}</div></div>'
        f'{body_html}</div>'
    )


def generate_audit_page(lead_id: int, lead: dict, seo: list = None,
                         competitors: list = None, roi: dict = None) -> str:
    token = secrets.token_urlsafe(16)[:16]
    now = datetime.datetime.now()
    expires_at = now + datetime.timedelta(hours=AUDIT_PAGE_EXPIRY_HOURS)
    name = lead["business_name"]
    name_escaped = html.escape(name)
    name_urlencoded = urllib.parse.quote(name)
    score = lead.get("lead_score", 0)
    monthly_loss = (roi or {}).get("total_monthly_loss", lead.get("estimated_monthly_loss", 0))
    annual_loss = monthly_loss * 12
    impacts = (roi or {}).get("impacts", [])
    currency_symbol = (roi or {}).get("currency_symbol") or get_currency_for_country(lead.get("country", "")).get("symbol", "$")
    cs = currency_symbol
    lang = get_language_for_country(lead.get("country", ""))

    pains = json.loads(lead.get("pain_points") or "[]")
    ops_pains = json.loads(lead.get("ops_pain_points") or "[]")
    niche = (lead.get("niche") or "").lower()
    social_proof = NICHE_SOCIAL_PROOF.get(niche, SOCIAL_PROOF_TEXT)
    social_proof_escaped = html.escape(social_proof)

    hours_left = max(0, int((expires_at - now).total_seconds()) // 3600)
    expiry_display = _t("expires_in", lang).format(hours_left) if hours_left > 0 else _t("expired", lang)

    score_label = _t("urgent", lang) if score >= 60 else _t("improve", lang) if score >= 40 else _t("decent", lang)
    score_color = "#ef4444" if score >= 60 else "#f59e0b" if score >= 40 else "#22c55e"
    score_bg = "#fef2f2" if score >= 60 else "#fffbeb" if score >= 40 else "#f0fdf4"

    tech_stack = json.loads(lead.get("tech_stack_json") or "{}")
    tech_items = []
    for cat, tools in tech_stack.items():
        if tools == ["none"]:
            tech_items.append(f'<div class="tech-row tech-missing"><svg width="16" height="16" viewBox="0 0 16 16"><circle cx="8" cy="8" r="7" fill="#fecaca"/><path d="M5 5l6 6M11 5l-6 6" stroke="#ef4444" stroke-width="1.5" stroke-linecap="round"/></svg><span class="tech-label">{cat.replace("_"," ").title()}</span><span class="tech-val tech-val-miss">{_t("not_detected", lang)}</span></div>')
        else:
            tech_items.append(f'<div class="tech-row tech-found"><svg width="16" height="16" viewBox="0 0 16 16"><circle cx="8" cy="8" r="7" fill="#bbf7d0"/><path d="M4.5 8l2.5 2.5 4.5-5" stroke="#22c55e" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg><span class="tech-label">{html.escape(cat.replace("_"," ").title())}</span><span class="tech-val">{", ".join(html.escape(t.replace("_"," ").title()) for t in tools)}</span></div>')
    tech_html = "\n".join(tech_items)

    impacts_rows = ""
    for imp in impacts[:8]:
        issue_escaped = html.escape(imp["issue"])
        impacts_rows += f'''<tr><td class="impact-issue">{issue_escaped}</td><td class="impact-loss">-{cs}{imp["monthly_loss"]:,}{_t("per_month", lang)}</td></tr>'''

    seo_rows = ""
    for r in (seo or [])[:5]:
        pos = r.get("position")
        kw = html.escape(r.get("keyword", ""))
        if pos and pos <= 3:
            badge_cls = "seo-good"
        elif pos and pos <= 10:
            badge_cls = "seo-ok"
        else:
            badge_cls = "seo-bad"
        pos_text = f"#{pos}" if pos else _t("not_found", lang)
        seo_rows += f'''<tr><td class="seo-kw">"{kw}"</td><td><span class="seo-badge {badge_cls}">{pos_text}</span></td></tr>'''

    comp_rows = ""
    for c in (competitors or [])[:3]:
        cn = html.escape(c.get("name", c.get("business_name", "?")))
        badges = ""
        for key, label in [("has_ssl","SSL"),("is_mobile_friendly",_t("mobile",lang)),("has_tracking_pixel",_t("tracking",lang))]:
            v = c.get(key)
            if v:
                badges += f'<span class="comp-badge comp-yes">{label}</span>'
            else:
                badges += f'<span class="comp-badge comp-no">{label}</span>'
        comp_rows += f'''<div class="comp-row"><div class="comp-name">{cn}</div><div class="comp-badges">{badges}<span class="comp-rating">★ {c.get("rating",0)}</span></div></div>'''

    revenue_card = _card("sun", "red", _t("revenue_impact", lang),
        f'<table class="impact-table">{impacts_rows}</table>'
        f'<div class="impact-total"><span class="impact-total-label">{_t("total_monthly", lang)}</span>'
        f'<span class="impact-total-val">{cs}{monthly_loss:,}{_t("per_month", lang)}</span></div>'
        f'<div class="chart-container"><canvas id="revenueChart"></canvas></div>'
    ) if impacts_rows else ""

    tech_card = _card("monitor", "blue", _t("tech_stack", lang), tech_html) if tech_html else ""

    seo_card = _card("search", "green", _t("search_visibility", lang),
        f'<table class="seo-table">{seo_rows}</table>'
    ) if seo_rows else ""

    comp_card = _card("users", "amber", _t("competitors", lang), comp_rows) if comp_rows else ""

    score_card_body = (
        '<div class="score-section"><div class="score-ring-wrap">'
        '<div class="score-ring">'
        f'<svg viewBox="0 0 100 100"><circle class="bg" cx="50" cy="50" r="40"/>'
        f'<circle class="fg" cx="50" cy="50" r="40"/></svg>'
        f'<span>{score}</span></div></div>'
        f'<div class="score-desc">{score_label}</div>'
        f'<span class="score-badge">{score}/100</span></div>'
    )
    score_card = _card("pulse", "blue", _t("health_score", lang), score_card_body)

    capture_card_body = (
        f'<p style="color:var(--text2);font-size:14px;margin-bottom:16px">{_t("enter_email", lang)}</p>'
        '<form id="captureForm" onsubmit="return false;">'
        f'<input type="email" class="form-input" placeholder="{_t("email_placeholder", lang)}" required id="emailInput">'
        f'<input type="hidden" id="token" value="{token}">'
        f'<button class="form-submit" onclick="submitCapture()">{_t("send_report", lang)}</button>'
        '</form>'
        '<div id="formResponse" style="margin-top:12px;font-size:14px;display:none"></div>'
    )
    capture_card = _card("mail", "", _t("full_report", lang), capture_card_body)
    capture_card = capture_card.replace('class="card">', 'class="card capture-card">', 1)

    clock_svg = _ICONS["clock"]

    page_html = f'''<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_t("audit_title", lang)} — {name_escaped}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{--bg:#f8fafc;--card:#ffffff;--border:#e2e8f0;--text:#0f172a;--text2:#475569;--text3:#94a3b8;--primary:#2563eb;--primary-dark:#1d4ed8;--red:#ef4444;--red-bg:#fef2f2;--red-border:#fecaca;--amber:#f59e0b;--amber-bg:#fffbeb;--green:#22c55e;--green-bg:#f0fdf4;--green-border:#bbf7d0;--radius:12px;--shadow:0 1px 3px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.04);--shadow-lg:0 10px 25px rgba(0,0,0,.08)}}
body{{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);line-height:1.6;-webkit-font-smoothing:antialiased}}
.container{{max-width:640px;margin:0 auto;padding:32px 20px}}

.header{{background:linear-gradient(135deg,#0f172a 0%,#1e293b 50%,#0f172a 100%);color:#fff;padding:48px 36px 40px;border-radius:20px;margin-bottom:28px;position:relative;overflow:hidden;box-shadow:0 20px 60px rgba(15,23,42,.35)}}
.header::before{{content:'';position:absolute;top:-50%;right:-50%;width:200%;height:200%;background:radial-gradient(circle,rgba(59,130,246,.08) 0%,transparent 60%);pointer-events:none}}
.header-label{{font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#60a5fa;margin-bottom:12px}}
.header h1{{font-size:22px;font-weight:700;margin-bottom:4px;position:relative}}
.header-sub{{color:#94a3b8;font-size:14px;font-weight:400;position:relative}}
.loss-block{{margin:32px 0 20px;position:relative}}
.loss-amount{{font-size:44px;font-weight:800;letter-spacing:-1px;color:#f87171;line-height:1.1}}
.loss-amount .period{{font-size:18px;font-weight:500;color:#94a3b8;margin-left:2px}}
.loss-label{{font-size:13px;color:#94a3b8;margin-top:6px;font-weight:500}}
.loss-annual{{margin-top:10px;font-size:13px;color:#64748b;font-weight:500}}
.loss-annual strong{{color:#fbbf24;font-weight:700}}
.expiry-pill{{display:inline-flex;align-items:center;gap:6px;margin-top:16px;padding:6px 14px;background:rgba(251,191,36,.1);border:1px solid rgba(251,191,36,.25);border-radius:100px;font-size:12px;color:#fbbf24;font-weight:500}}

.card{{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:28px;margin-bottom:20px;box-shadow:var(--shadow)}}
.card-header{{display:flex;align-items:center;gap:10px;margin-bottom:20px;padding-bottom:14px;border-bottom:1px solid var(--border)}}
.card-icon{{width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:15px;flex-shrink:0}}
.card-icon.red{{background:var(--red-bg);color:var(--red)}}
.card-icon.blue{{background:#eff6ff;color:var(--primary)}}
.card-icon.green{{background:var(--green-bg);color:var(--green)}}
.card-icon.amber{{background:var(--amber-bg);color:var(--amber)}}
.card-title{{font-size:15px;font-weight:700;color:var(--text)}}

.impact-table{{width:100%;border-collapse:collapse}}
.impact-table td{{padding:12px 0;border-bottom:1px solid #f1f5f9;font-size:14px}}
.impact-issue{{color:var(--text2);font-weight:500}}
.impact-loss{{color:var(--red);font-weight:700;text-align:right;white-space:nowrap}}
.impact-total{{display:flex;justify-content:space-between;align-items:center;padding:16px 0 0;margin-top:4px;border-top:2px solid var(--text)}}
.impact-total-label{{font-weight:700;font-size:14px}}
.impact-total-val{{color:var(--red);font-size:20px;font-weight:800}}

.tech-row{{display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid #f8fafc}}
.tech-row:last-child{{border-bottom:none;padding-bottom:0}}
.tech-label{{font-weight:600;font-size:13px;color:var(--text);min-width:110px}}
.tech-val{{font-size:13px;color:var(--text2)}}
.tech-val-miss{{color:var(--red);font-weight:600}}

.seo-table{{width:100%;border-collapse:collapse}}
.seo-table td{{padding:10px 0;border-bottom:1px solid #f8fafc;font-size:14px}}
.seo-kw{{color:var(--text2);font-weight:500}}
.seo-badge{{display:inline-block;padding:2px 10px;border-radius:100px;font-size:12px;font-weight:700}}
.seo-good{{background:var(--green-bg);color:#15803d}}
.seo-ok{{background:var(--amber-bg);color:#b45309}}
.seo-bad{{background:var(--red-bg);color:#dc2626}}

.comp-row{{padding:14px 16px;background:#f8fafc;border-radius:10px;margin-bottom:8px;border:1px solid #f1f5f9}}
.comp-name{{font-weight:600;font-size:14px;color:var(--text);margin-bottom:8px}}
.comp-badges{{display:flex;flex-wrap:wrap;gap:6px;align-items:center}}
.comp-badge{{padding:3px 10px;border-radius:100px;font-size:11px;font-weight:600}}
.comp-yes{{background:var(--green-bg);color:#15803d}}
.comp-no{{background:var(--red-bg);color:#dc2626}}
.comp-rating{{padding:3px 10px;border-radius:100px;font-size:11px;font-weight:600;background:#f1f5f9;color:var(--text2);margin-left:auto}}

.score-section{{text-align:center;padding:12px 0}}
.score-ring-wrap{{display:inline-block;position:relative}}
.score-ring{{width:100px;height:100px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:32px;font-weight:800;position:relative}}
.score-ring svg{{position:absolute;top:0;left:0;width:100%;height:100%;transform:rotate(-90deg)}}
.score-ring circle{{fill:none;stroke-width:6;stroke-linecap:round}}
.score-ring .bg{{stroke:#f1f5f9}}
.score-ring .fg{{stroke:{score_color};stroke-dasharray:251.3;stroke-dashoffset:{251.3 - (251.3 * min(score, 100) / 100)}}}
.score-desc{{color:var(--text2);font-size:14px;margin-top:12px;font-weight:500}}
.score-badge{{display:inline-block;margin-top:8px;padding:4px 14px;border-radius:100px;font-size:12px;font-weight:600;background:{score_bg};color:{score_color};border:1px solid {score_color}30}}

.capture-card{{border:2px solid #eff6ff;background:linear-gradient(180deg,#fafbff 0%,#fff 100%)}}
.capture-card .card-icon{{background:#eff6ff;color:var(--primary)}}
.form-input{{width:100%;padding:12px 16px;border:1.5px solid var(--border);border-radius:10px;margin-bottom:12px;font-size:14px;font-family:inherit;transition:border-color .2s,box-shadow .2s;outline:none;color:var(--text)}}
.form-input:focus{{border-color:var(--primary);box-shadow:0 0 0 3px rgba(37,99,235,.1)}}
.form-submit{{width:100%;background:var(--primary);color:white;border:none;padding:13px 20px;border-radius:10px;font-weight:600;font-size:15px;cursor:pointer;transition:background .2s,transform .1s;font-family:inherit}}
.form-submit:hover{{background:var(--primary-dark)}}
.form-submit:active{{transform:scale(.98)}}

.cta{{display:block;width:100%;background:linear-gradient(135deg,#059669,#10b981);color:#fff;text-align:center;padding:16px;border-radius:12px;font-weight:600;font-size:15px;text-decoration:none;margin-top:20px;box-shadow:0 4px 14px rgba(5,150,105,.3);transition:transform .15s,box-shadow .15s}}
.cta:hover{{transform:translateY(-1px);box-shadow:0 6px 20px rgba(5,150,105,.4)}}
.cta:active{{transform:translateY(0)}}

.proof{{text-align:center;background:#f0f9ff;border-color:#bae6fd;overflow:hidden;position:relative}}
.proof::before{{content:'"';position:absolute;top:-8px;left:20px;font-size:80px;color:#bae6fd;font-family:Georgia,serif;line-height:1}}
.proof-text{{color:#0369a1;font-size:14px;line-height:1.6;font-style:italic;position:relative;font-weight:500}}

.footer{{text-align:center;color:var(--text3);font-size:12px;margin-top:36px;padding:20px 0;border-top:1px solid var(--border)}}
.footer-brand{{font-weight:600;color:var(--text2)}}

.chart-container{{position:relative;height:180px;margin-top:16px}}

@media(max-width:480px){{
  .header{{padding:36px 24px 32px;border-radius:16px}}
  .loss-amount{{font-size:34px}}
  .container{{padding:20px 14px}}
  .card{{padding:20px}}
}}

@keyframes fadeInUp{{from{{opacity:0;transform:translateY(12px)}}to{{opacity:1;transform:translateY(0)}}}}
.card{{animation:fadeInUp .4s ease both}}
.card:nth-child(2){{animation-delay:.05s}}
.card:nth-child(3){{animation-delay:.1s}}
.card:nth-child(4){{animation-delay:.15s}}
.card:nth-child(5){{animation-delay:.2s}}
.card:nth-child(6){{animation-delay:.25s}}
</style>
<img src="{BASE_URL}/t/audit/{token}.gif" width="1" height="1" style="display:none">
</head>
<body>
<div class="container">

  <div class="header">
    <div class="header-label">{_t("audit_title", lang)}</div>
    <h1>{name_escaped}</h1>
    <div class="header-sub">{_t("prepared_for", lang)} {name_escaped}</div>
    <div class="loss-block">
      <div class="loss-amount">{cs}{monthly_loss:,}<span class="period">{_t("per_month", lang)}</span></div>
      <div class="loss-label">{_t("revenue_left", lang)}</div>
      <div class="loss-annual">{_t("thats", lang)} <strong>{cs}{annual_loss:,}{_t("per_year", lang)}</strong></div>
    </div>
    <div class="expiry-pill">
      {clock_svg}
      <span id="countdown">{expiry_display}</span>
    </div>
  </div>

  {revenue_card}
  {tech_card}
  {seo_card}
  {comp_card}
  {score_card}
  {capture_card}

  <a class="cta" href="mailto:{YOUR_EMAIL}?subject=Audit%20for%20{name_urlencoded}">{_t("book_call", lang)}</a>

  <div class="card proof">
    <div class="proof-text">{social_proof_escaped}</div>
  </div>

  <div class="footer">
    <span class="footer-brand">{YOUR_COMPANY}</span> · {_t("generated", lang)} · <span id="viewCounter">0</span> {_t("views", lang)}
  </div>

</div>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
let expirySeconds = {hours_left} * 3600;
function updateCountdown() {{
  if (expirySeconds <= 0) {{ document.getElementById('countdown').textContent = '{_t("expired", lang)}'; return; }}
  const h = Math.floor(expirySeconds / 3600);
  const m = Math.floor((expirySeconds % 3600) / 60);
  const s = expirySeconds % 60;
  document.getElementById('countdown').textContent = h + 'h ' + m + 'm ' + s + 's';
  expirySeconds--;
}}
setInterval(updateCountdown, 1000);
updateCountdown();

const impacts = {json.dumps(impacts[:8])};
if (impacts.length > 0) {{
  const ctx = document.getElementById('revenueChart');
  if (ctx) new Chart(ctx.getContext('2d'), {{
    type: 'bar',
    data: {{
      labels: impacts.map(i => i.issue.length > 22 ? i.issue.substring(0, 22) + '…' : i.issue),
      datasets: [{{label: {_t("monthly_loss_chart", lang)} + ' (' + {json.dumps(currency_symbol)} + ')', data: impacts.map(i => i.monthly_loss), backgroundColor: 'rgba(239,68,68,.15)', borderColor: '#ef4444', borderWidth: 1.5, borderRadius: 6, maxBarThickness: 48}}]
    }},
    options: {{responsive: true, maintainAspectRatio: false, plugins: {{legend: {{display: false}}}}, scales: {{y: {{beginAtZero: true, ticks: {{callback: v => {json.dumps(currency_symbol)} + v.toLocaleString(), font: {{size: 11}}}}, grid: {{color: '#f1f5f9'}}}}, x: {{ticks: {{font: {{size: 10}}, maxRotation: 45, minRotation: 0}}, grid: {{display: false}}}}}}}}
  }});
}}

if (typeof localStorage !== 'undefined') {{
  const key = 'view_' + '{token}';
  let views = parseInt(localStorage.getItem(key) || '0');
  views++;
  localStorage.setItem(key, views.toString());
  document.getElementById('viewCounter').textContent = views;
}}

async function submitCapture() {{
  const email = document.getElementById('emailInput').value.trim();
  const token = document.getElementById('token').value;
  if (!email) return;
  const el = document.getElementById('formResponse');
  el.style.display = 'block';
  el.textContent = 'Sending...';
  el.style.color = '#f59e0b';
  try {{
    const res = await fetch('{BASE_URL}/api/webhook/capture', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{email, token}})}});
    const data = await res.json();
    if (res.ok) {{ el.textContent = '✓ Report sent!'; el.style.color = '#22c55e'; }}
    else {{ el.textContent = '✗ ' + (data.error || 'Failed'); el.style.color = '#ef4444'; }}
  }} catch {{ el.textContent = '✗ Network error'; el.style.color = '#ef4444'; }}
}}
</script>
</body></html>'''

    audits_dir = os.path.join(os.path.dirname(__file__), "audits")
    os.makedirs(audits_dir, exist_ok=True)
    path = os.path.join(audits_dir, f"{token}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page_html)

    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET audit_page_token=?, audit_page_generated_at=?, audit_page_expires_at=? WHERE id=?",
            (token, now.isoformat(), expires_at.isoformat(), lead_id))
        page_path = f"/audit/{token}"
        try:
            conn.execute(
                """INSERT INTO proposals (lead_id, page_path, kind) VALUES (?,?, 'audit_page')
                   ON CONFLICT(lead_id, kind) DO UPDATE SET
                       page_path=excluded.page_path,
                       created_at=datetime('now')""",
                (lead_id, page_path))
        except Exception:
            cur = conn.execute(
                "UPDATE proposals SET page_path=? WHERE lead_id=? AND kind='audit_page'",
                (page_path, lead_id))
            if cur.rowcount == 0:
                conn.execute(
                    "INSERT INTO proposals (lead_id, page_path, kind) VALUES (?,?,?)",
                    (lead_id, page_path, "audit_page"))

    return f"/audit/{token}"


def generate_audit_preview(lead: dict, token: str) -> str:
    pains = json.loads(lead.get("pain_points") or "[]")
    ops_pains = json.loads(lead.get("ops_pain_points") or "[]")
    monthly_loss = lead.get("estimated_monthly_loss", 0)
    score = lead.get("lead_score", 0)
    cs = get_currency_for_country(lead.get("country", "")).get("symbol", "$")

    top_pains = pains[:2]
    top_ops = [p.get("pain", "") for p in ops_pains[:2] if isinstance(p, dict)]

    preview_parts = []
    if monthly_loss > 0:
        preview_parts.append(f"{cs}{monthly_loss:,}/mo in estimated lost revenue")
    if top_pains:
        preview_parts.append(f"{top_pains[0]}")
    elif top_ops:
        preview_parts.append(f"{top_ops[0]}")

    if score >= 60:
        preview_parts.append("digital health score flagged as critical")
    elif score >= 40:
        preview_parts.append("digital health score below industry average")

    preview = " — ".join(preview_parts[:3])
    return preview if preview else "key digital gaps and revenue opportunities"


def delete_expired_audit_pages():
    """Delete audit pages that have expired (past audit_page_expires_at)."""
    import os, datetime
    audits_dir = os.path.join(os.path.dirname(__file__), "audits")
    if not os.path.exists(audits_dir):
        return 0

    with get_conn() as conn:
        expired = conn.execute(
            "SELECT id, audit_page_token FROM leads WHERE audit_page_expires_at IS NOT NULL AND audit_page_expires_at < datetime('now')"
        ).fetchall()

        deleted_count = 0
        for row in expired:
            token = row["audit_page_token"]
            if token:
                file_path = os.path.join(audits_dir, f"{token}.html")
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        deleted_count += 1
                except OSError:
                    pass
                conn.execute(
                    "UPDATE leads SET audit_page_token=NULL, audit_page_generated_at=NULL, audit_page_expires_at=NULL WHERE id=?",
                    (row["id"],))
        return deleted_count
