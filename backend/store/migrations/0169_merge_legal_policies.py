"""
LEGAL-POLICIES-01 — joins the hotfix branch to master's migrations.

`0165_company_legal_policies` was shipped on its own on top of 0164 (the schema in
production at the time), while master already carried 0165–0168. The two branches
touch disjoint tables, so this node changes no schema: it only tells Django the
order, exactly like `0041_merge_checkout_and_commercial_graphs`.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0168_catalog_aliases'),
        ('store', '0165_company_legal_policies'),
    ]

    operations = []
