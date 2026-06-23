-- Founding 20 free cohort: plan tracking + cap enforcement support
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS plan TEXT DEFAULT 'founding_free';
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS founding_cohort BOOLEAN DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_tenants_founding_cohort ON tenants(founding_cohort) WHERE founding_cohort = true;
