CREATE TABLE IF NOT EXISTS hospital_capacity (
  hospital_id VARCHAR(20) PRIMARY KEY,
  beds_total INT NOT NULL,
  beds_free INT NOT NULL,
  staff_total INT NOT NULL,
  staff_free INT NOT NULL,
  ventilators_total INT NOT NULL,
  ventilators_free INT NOT NULL,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS decision_logs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  hospital_id VARCHAR(20) NOT NULL,
  predicted_load VARCHAR(20) NOT NULL,
  action VARCHAR(100) NOT NULL,
  resource_status VARCHAR(30) NOT NULL,
  beds_needed INT NOT NULL,
  staff_needed INT NOT NULL,
  ventilators_needed INT NOT NULL,
  beds_reserved INT NOT NULL,
  staff_reserved INT NOT NULL,
  ventilators_reserved INT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
