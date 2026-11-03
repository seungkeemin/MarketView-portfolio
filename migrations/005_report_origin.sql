-- MarketView 005: 리포트가 어떻게 만들어졌는지 기록한다 (사용자가 눌렀나, 앱이 자동으로 만들었나, 꺼져 있던 날을 따라잡았나).
ALTER TABLE report_run ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual' CHECK (origin IN ('manual','auto','catchup'));
