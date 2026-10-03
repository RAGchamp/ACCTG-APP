"""Small Business Accounting Agent - MVP 1 (Flask app).

Plan: ..\\INFO\\SMALL-BIZ-ACCTG-MVP1-PLAN.md

  ingest/     step 1-2: intake (dedupe, page images), extraction (Claude or CSV), checks
  books/      the ledger: schema, posting, matching, proposals (Claude), review, reconciliation
  reports/    financial statements from the GL (never Claude)
  tax/        OS-114, Schedule C/SE, Form 4562 figures, 1099 list, CT figures - from tax_rules\\<year>
  testdata/   the synthetic test data generator, sandboxes and the scorecard (plan §15)
  web/        the screens (plan §11); doc_review.py = Review input documents (DOC-REVIEW-REPROCESS-PLAN.md)

Run:  python app.py   ->  http://127.0.0.1:5050
"""

import logging

from flask import Flask

import config
from web import api, doc_review, pages

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.FileHandler(config.LOG_DIR / "app.log", encoding="utf-8"), logging.StreamHandler()],
)
logging.getLogger("werkzeug").addFilter(lambda record: "/api/jobs/" not in record.getMessage())


def create_app():
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024
    app.register_blueprint(pages.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(doc_review.bp)
    app.register_blueprint(doc_review.api_bp)
    pages.register_filters(app)
    return app


app = create_app()

if __name__ == "__main__":
    print(f"Small Business Accounting Agent: http://{config.HOST}:{config.PORT}")
    app.run(host=config.HOST, port=config.PORT, debug=False, threaded=True)
