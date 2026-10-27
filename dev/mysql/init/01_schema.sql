
USE qa_eval_demo;

CREATE TABLE jhsjk_article (
    id CHAR(32) NOT NULL COMMENT '主键UUID（无连字符）',

    article_id VARCHAR(64) DEFAULT NULL
        COMMENT '站点文章ID，用于增量去重',

    title VARCHAR(500) DEFAULT NULL
        COMMENT '文章标题',

    url VARCHAR(2048) DEFAULT NULL
        COMMENT '文章链接',

    pub_date DATE DEFAULT NULL
        COMMENT '发布日期',

    origin_name VARCHAR(255) DEFAULT NULL
        COMMENT '文章来源',

    summary TEXT
        COMMENT '列表接口返回的内容摘要',

    content LONGTEXT
        COMMENT 'HTML格式正文',

    plain_text_content LONGTEXT
        COMMENT '纯文本正文',

    create_time DATETIME DEFAULT NULL
        COMMENT '创建时间',

    PRIMARY KEY (id),

    INDEX idx_article_id (article_id),
    INDEX idx_pub_date (pub_date),
    INDEX idx_create_time (create_time)
)
ENGINE = InnoDB
DEFAULT CHARSET = utf8mb4
COLLATE = utf8mb4_unicode_ci
COMMENT = '文章数据集模拟表';
