-- MySQL dump 10.13  Distrib 8.0.32, for Linux (x86_64)
-- Host: localhost    Database: prod_env

DROP TABLE IF EXISTS `system_config`;
CREATE TABLE `system_config` (
  `id` int NOT NULL AUTO_INCREMENT,
  `config_key` varchar(255) NOT NULL,
  `config_value` varchar(255) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

LOCK TABLES `system_config` WRITE;
INSERT INTO `system_config` VALUES 
(1,'max_db_connections','500'),
(2,'cache_ttl_seconds','600'),
(3,'api_rate_limit','100'),
(4,'default_theme','dark'),
(5,'worker_timeout','30');
UNLOCK TABLES;