"""Process durable email document jobs without delaying mailbox receive cadence."""
from app.email_worker import main


if __name__ == "__main__":
    main(mode="extract")
