"""One-time script: Add new columns to companies table and backfill data."""
import asyncio
import sys
import os

# Ensure the project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def add_columns():
    from backend.core.database import create_worker_session_factory
    from sqlalchemy import text
    factory = create_worker_session_factory()
    async with factory() as db:
        for col in ["market_cap_value FLOAT", "revenue_last_fy FLOAT", "employee_count INTEGER"]:
            try:
                await db.execute(text(f"ALTER TABLE companies ADD COLUMN IF NOT EXISTS {col}"))
            except Exception as e:
                print(f"Column may already exist: {e}")
        await db.commit()
    print("Columns added (or already existed).")


async def backfill():
    from backend.core.database import create_worker_session_factory
    from sqlalchemy import text
    factory = create_worker_session_factory()

    data = [
        ("ICICI Bank Ltd", 875000, 230000, 130000),
        ("YES Bank Ltd", 25000, 28000, 25000),
        ("IDFC First Bank Ltd", 45000, 38000, 30000),
        ("Waaree Energies Ltd", 60000, 12000, 5000),
        ("Singularity AMC Pvt Ltd", 500, 50, 100),
        ("Adani Power Ltd", 250000, 55000, 15000),
        ("JSW Energy Ltd", 120000, 18000, 8000),
    ]

    async with factory() as db:
        for name, mcv, rev, emp in data:
            result = await db.execute(text(
                "UPDATE companies SET market_cap_value = :mcv, revenue_last_fy = :rev, employee_count = :emp WHERE name = :name"
            ), {'mcv': mcv, 'rev': rev, 'emp': emp, 'name': name})
            print(f"  {name}: {result.rowcount} row(s) updated")
        await db.commit()
    print("Backfill complete.")


async def main():
    await add_columns()
    await backfill()


if __name__ == "__main__":
    asyncio.run(main())
