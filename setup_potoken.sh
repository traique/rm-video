#!/bin/sh
# Chỉ dùng khi có shell. Trên Botkeep: gõ  setup_potoken  vào ô console là đủ.
cd "$(dirname "$0")" && python3 -c "import asyncio, potoken_setup; asyncio.run(potoken_setup.install(print))"
