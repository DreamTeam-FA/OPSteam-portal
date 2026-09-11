"""
recategorize.py
Re-assigns categories for specific documents in library_chunks.
Run once: python recategorize.py
"""
from dotenv import load_dotenv
load_dotenv()

from database import SessionLocal
from sqlalchemy import text

# Rules: (fragment to match in file_name, new_category)
RULES = [
    # Invoice Automation → SOPs & Processes
    ("Invoice Automation",          "SOPs & Processes",  None),
    # 5-Minute Course Review → Digital Course Academy
    ("5-Minute Course Review",      "Digital Course Academy", "Resources"),
    ("5-minute Course Review",      "Digital Course Academy", "Resources"),
    # Tabme → Resources/Tools
    ("Tabme",                       "Resources",         "Tools & Templates"),
    # Voice Sprint → Content Week
    ("Voice Sprint",                "Content Week",      None),
    # Bootcamp Workshop → AI Advantage
    ("Bootcamp Workshop",           "AI Advantage",      None),
    # MasterClass (generic doc) → AI Advantage
    ("MasterClass",                 "AI Advantage",      None),
    # Resource Library, Hacks → keep as Resources
]

def main():
    with SessionLocal() as db:
        for name_frag, new_cat, new_sub in RULES:
            result = db.execute(
                text("""
                    UPDATE library_chunks
                    SET category = :cat
                    WHERE file_name ILIKE :frag
                      AND category != :cat
                    RETURNING file_name
                """),
                {"cat": new_cat, "frag": f"%{name_frag}%"}
            )
            rows = result.fetchall()
            if rows:
                names = set(r.file_name for r in rows)
                print(f"✅ '{name_frag}' → {new_cat}: {', '.join(names)}")
            else:
                print(f"⏭  '{name_frag}' — no changes needed")

        db.commit()
    print("\nDone.")

if __name__ == "__main__":
    main()
