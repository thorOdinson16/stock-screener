### Convert start and stop scripts into executable

```bash
chmod +x start-stack.sh stop-stack.sh
```

### Start the Stack
```bash
./start-stack.sh
```

### Convert Kafka Topic into executable
```bash
chmod +x kafka/topics/create-topics.sh
```

### Create Topics after starting stack
```bash
./kafka/topics/create-topics.sh
```

### Setup for Poller
```bash
cd poller
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# small test first — don't hit all 500 symbols on the first run
python poller.py --once
```

### Verify
```bash
$KAFKA_HOME/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic market.quotes --from-beginning --max-messages 5
```