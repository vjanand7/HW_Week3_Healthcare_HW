-- One-time database setup. Run as a PostgreSQL admin (e.g. the "postgres" user):
--   psql -U postgres -f seed_or_setup.sql
-- Then create the tables:
--   psql -U fde_user -d fde_db -f schema.sql
-- Then load the dataset:
--   python data_loader.py

-- 1. App user and database. Replace the placeholder password, and use the same
--    value in your .env file (DATABASE_URL).
CREATE USER fde_user WITH PASSWORD 'change-me';
CREATE DATABASE fde_db OWNER fde_user;

-- 2. OPTIONAL demo data, for trying the API before loading the full dataset.
--    Run it against fde_db after schema.sql:  psql -U fde_user -d fde_db
--    Uncomment, run, and delete these rows again before loading the real data
--    if you want a clean count.
--
-- INSERT INTO patient_encounters
--     (encounter_id, patient_nbr, age, time_in_hospital, number_inpatient, number_emergency,
--      number_outpatient, num_medications, num_lab_procedures, discharge_disposition_id,
--      admission_type_id, readmitted, risk_score, risk_level)
-- VALUES
--     ('demo-1', 'demo-p1', '[70-80)', 9, 4, 2, 1, 25, 60, 1, 1, '<30', 7, 'HIGH'),
--     ('demo-2', 'demo-p2', '[50-60)', 4, 3, 0, 0, 12, 40, 1, 1, 'NO',  3, 'MEDIUM'),
--     ('demo-3', 'demo-p3', '[30-40)', 2, 0, 0, 0,  5, 20, 1, 1, 'NO',  0, 'LOW');
-- INSERT INTO care_interventions (encounter_id, status, assigned_to, notes)
-- VALUES ('demo-1', 'ASSIGNED', 'Care Team A', 'Demo case');
