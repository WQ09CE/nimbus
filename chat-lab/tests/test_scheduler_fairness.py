from uuid import uuid4

from test_agent_review import prepared


async def test_64_prepared_future_deliveries_do_not_block_new_immediate_run(store):
    # Four synthetic authorized users, each with the permitted 16 daily jobs.
    # Their early results are ready, but deliberately cannot be delivered yet.
    for user in range(2, 6):
        await store.authorize(100, user, user)
        session = uuid4()
        async with await store.connect() as c:
            await c.execute(
                "INSERT INTO sessions(id,bot_id,user_id,chat_id) VALUES (%s,100,%s,%s)",
                (session, user, user),
            )
            for i in range(16):
                sid, tid, rid = uuid4(), uuid4(), uuid4()
                await c.execute(
                    """INSERT INTO schedules(id,bot_id,user_id,chat_id,name,instructions,timezone,hour,minute,enabled,next_run)
                    VALUES (%s,100,%s,%s,%s,'future','UTC',8,0,true,clock_timestamp()+interval '1 day')""",
                    (sid, user, user, f"future-{i}"),
                )
                await c.execute(
                    "INSERT INTO turns(id,session_id,epoch,state,input,result) VALUES (%s,%s,0,'succeeded','future','ready')",
                    (tid, session),
                )
                await c.execute(
                    """INSERT INTO schedule_runs(id,schedule_id,generation,slot,turn_id,state,expires_at)
                    VALUES (%s,%s,1,clock_timestamp()+interval '25 minutes',%s,'attached',clock_timestamp()+interval '4 hours')""",
                    (rid, sid, tid),
                )
    # Newer run_now should attach immediately, not wait behind those 64 rows.
    _, _, job = await prepared(store)
    assert job is not None
