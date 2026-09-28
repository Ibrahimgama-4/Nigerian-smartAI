"""GENERALIZED soil/fertilizer guidance. No dosages. No interpretation of N/P/K numbers
because units and lab method are unknown. Always states whether a soil test was provided."""
LEGUMES = {"cowpea", "groundnut", "soybean", "beans"}

def soil_advice(s: dict) -> dict:
    tips, has_test = [], any(s.get(k) is not None for k in ("ph", "organic_matter_pct", "nitrogen", "phosphorus", "potassium"))
    ph, om = s.get("ph"), s.get("organic_matter_pct")
    if ph is not None:
        if ph < 5.5:
            tips.append("Soil is strongly acidic. Acidity can reduce nutrient availability. A soil laboratory or extension officer can advise whether liming suits your soil and crop.")
        elif ph < 6.0:
            tips.append("Soil is slightly acidic. This is suitable for many crops; watch crops that prefer near-neutral soil.")
        elif ph <= 7.5:
            tips.append("Soil pH is in a range generally suitable for most crops.")
        elif ph <= 8.5:
            tips.append("Soil is alkaline. Some micronutrients such as iron and zinc can become less available; yellowing of young leaves can be one sign, but has other possible causes.")
        else:
            tips.append("Soil is strongly alkaline, which may indicate salt or sodium problems. Seek advice from a soil laboratory or extension officer.")
    if om is not None:
        if om < 1:
            tips.append("Organic matter is low. Adding compost, manure or returning crop residues can improve soil structure and water holding over time.")
        elif om < 3:
            tips.append("Organic matter is moderate. Continued additions of compost, manure or crop residues help maintain it.")
        else:
            tips.append("Organic matter is relatively good. Keep returning residues and avoid practices that strip the soil.")
    if any(s.get(k) is not None for k in ("nitrogen", "phosphorus", "potassium")):
        tips.append("How to read N, P and K values depends on the laboratory method and units"
                    + (f" (you entered: {s['npk_units_method']})" if s.get("npk_units_method") else " (none entered)")
                    + ". Ask the laboratory or an extension officer for fertilizer rates; this app does not give dosages.")
    prev = (s.get("previous_crop") or "").strip().lower()
    if prev in LEGUMES:
        tips.append("The previous crop was a legume, which can leave some nitrogen for the next crop. The amount varies, so it is not a substitute for a soil test.")
    elif prev:
        tips.append("Rotating between different crop families helps reduce pest and disease carry-over compared with repeating the same crop.")
    tips.append("Yellow or pale leaves can be caused by nutrient shortage, water stress, disease or pests. Scan the plant and check the field before treating for a nutrient problem.")
    return {"basis": "soil_test_values_entered" if has_test else "generalized_no_soil_test",
            "notice": ("Based on the values you entered; still general guidance." if has_test else
                       "No soil test values entered, so this guidance is generalized. A soil test gives much better advice."),
            "tips": tips, "timing": "Ask your extension officer for local timing of fertilizer and organic matter applications for your crop and stage."}
