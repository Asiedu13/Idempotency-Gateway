import httpx
import asyncio
import uuid

BASE_URL = "http://127.0.0.1:8000/api/v1"

async def send_payment(client, key, amount=100.00, label=""):
    response = await client.post(
        f"{BASE_URL}/process-payment",
        headers={"Idempotency-Key": key},
        json={
            "amount": amount,
            "phone_number": "+233245584914",
            "email": "princekofasiedu@gmail.com",
            "full_name": "Prince Kofi",
            "payment_method": "MOMO",
            "currency": "GHS"}
    )
    print(f"[{label}] {response.status_code} — {response.json()}")

async def main():
    async with httpx.AsyncClient(timeout=15) as client:

        # --- Scenario 1: Trigger the breaker (send 6 bad requests) ---
        print("\n=== Tripping the circuit breaker ===")
        for i in range(40):
            await send_payment(client, str(uuid.uuid4()), amount=500, label=f"REQUEST-{i+1}")

        # --- Scenario 2: Confirm it's OPEN ---
        # print("\n=== Breaker should be OPEN now ===")
        # r = await client.get(f"{BASE_URL}/circuit-breaker/status")
        # print(r.json())

        # --- Scenario 3: Send a good request while OPEN (should 503) ---
        print("\n=== Good request while OPEN ===")
        await send_payment(client, str(uuid.uuid4()), amount=200, label="GOOD-WHILE-OPEN")

        # --- Scenario 4: Race condition — 5 identical requests at once ---
        print("\n=== Race condition test ===")
        key = str(uuid.uuid4())
        await asyncio.gather(*[
            send_payment(client, key, amount=50, label=f"RACE-{i+1}")
            for i in range(50)
        ])

asyncio.run(main())