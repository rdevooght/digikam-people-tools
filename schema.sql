BEGIN TRANSACTION;
CREATE TABLE IF NOT EXISTS "AlbumRoots" (
	"id"	INTEGER,
	"label"	TEXT,
	"status"	INTEGER NOT NULL,
	"type"	INTEGER NOT NULL,
	"identifier"	TEXT,
	"specificPath"	TEXT,
	"caseSensitivity"	INTEGER,
	PRIMARY KEY("id"),
	UNIQUE("identifier","specificPath")
);
CREATE TABLE IF NOT EXISTS "Albums" (
	"id"	INTEGER,
	"albumRoot"	INTEGER NOT NULL,
	"relativePath"	TEXT NOT NULL,
	"date"	DATE,
	"caption"	TEXT,
	"collection"	TEXT,
	"icon"	INTEGER,
	"modificationDate"	DATETIME,
	UNIQUE("albumRoot","relativePath"),
	PRIMARY KEY("id")
);
CREATE TABLE IF NOT EXISTS "DownloadHistory" (
	"id"	INTEGER,
	"identifier"	TEXT,
	"filename"	TEXT,
	"filesize"	INTEGER,
	"filedate"	DATETIME,
	PRIMARY KEY("id"),
	UNIQUE("identifier","filename","filesize","filedate")
);
CREATE TABLE IF NOT EXISTS "ImageComments" (
	"id"	INTEGER,
	"imageid"	INTEGER,
	"type"	INTEGER,
	"language"	TEXT,
	"author"	TEXT,
	"date"	DATETIME,
	"comment"	TEXT,
	PRIMARY KEY("id"),
	UNIQUE("imageid","type","language","author")
);
CREATE TABLE IF NOT EXISTS "ImageCopyright" (
	"id"	INTEGER,
	"imageid"	INTEGER,
	"property"	TEXT,
	"value"	TEXT,
	"extraValue"	TEXT,
	PRIMARY KEY("id"),
	UNIQUE("imageid","property","value","extraValue")
);
CREATE TABLE IF NOT EXISTS "ImageHistory" (
	"imageid"	INTEGER,
	"uuid"	TEXT,
	"history"	TEXT,
	PRIMARY KEY("imageid")
);
CREATE TABLE IF NOT EXISTS "ImageInformation" (
	"imageid"	INTEGER,
	"rating"	INTEGER,
	"creationDate"	DATETIME,
	"digitizationDate"	DATETIME,
	"orientation"	INTEGER,
	"width"	INTEGER,
	"height"	INTEGER,
	"format"	TEXT,
	"colorDepth"	INTEGER,
	"colorModel"	INTEGER,
	PRIMARY KEY("imageid")
);
CREATE TABLE IF NOT EXISTS "ImageMetadata" (
	"imageid"	INTEGER,
	"make"	TEXT,
	"model"	TEXT,
	"lens"	TEXT,
	"aperture"	REAL,
	"focalLength"	REAL,
	"focalLength35"	REAL,
	"exposureTime"	REAL,
	"exposureProgram"	INTEGER,
	"exposureMode"	INTEGER,
	"sensitivity"	INTEGER,
	"flash"	INTEGER,
	"whiteBalance"	INTEGER,
	"whiteBalanceColorTemperature"	INTEGER,
	"meteringMode"	INTEGER,
	"subjectDistance"	REAL,
	"subjectDistanceCategory"	INTEGER,
	PRIMARY KEY("imageid")
);
CREATE TABLE IF NOT EXISTS "ImagePositions" (
	"imageid"	INTEGER,
	"latitude"	TEXT,
	"latitudeNumber"	REAL,
	"longitude"	TEXT,
	"longitudeNumber"	REAL,
	"altitude"	REAL,
	"orientation"	REAL,
	"tilt"	REAL,
	"roll"	REAL,
	"accuracy"	REAL,
	"description"	TEXT,
	PRIMARY KEY("imageid")
);
CREATE TABLE IF NOT EXISTS "ImageProperties" (
	"imageid"	INTEGER NOT NULL,
	"property"	TEXT NOT NULL,
	"value"	TEXT NOT NULL,
	UNIQUE("imageid","property")
);
CREATE TABLE IF NOT EXISTS "ImageRelations" (
	"subject"	INTEGER,
	"object"	INTEGER,
	"type"	INTEGER,
	UNIQUE("subject","object","type")
);
CREATE TABLE IF NOT EXISTS "ImageTagProperties" (
	"imageid"	INTEGER,
	"tagid"	INTEGER,
	"property"	TEXT,
	"value"	TEXT
);
CREATE TABLE IF NOT EXISTS "ImageTags" (
	"imageid"	INTEGER NOT NULL,
	"tagid"	INTEGER NOT NULL,
	UNIQUE("imageid","tagid")
);
CREATE TABLE IF NOT EXISTS "Images" (
	"id"	INTEGER,
	"album"	INTEGER,
	"name"	TEXT NOT NULL,
	"status"	INTEGER NOT NULL,
	"category"	INTEGER NOT NULL,
	"modificationDate"	DATETIME,
	"fileSize"	INTEGER,
	"uniqueHash"	TEXT,
	"manualOrder"	INTEGER,
	UNIQUE("album","name"),
	PRIMARY KEY("id")
);
CREATE TABLE IF NOT EXISTS "Searches" (
	"id"	INTEGER,
	"type"	INTEGER,
	"name"	TEXT NOT NULL,
	"query"	TEXT NOT NULL,
	PRIMARY KEY("id")
);
CREATE TABLE IF NOT EXISTS "Settings" (
	"keyword"	TEXT NOT NULL UNIQUE,
	"value"	TEXT
);
CREATE TABLE IF NOT EXISTS "TagProperties" (
	"tagid"	INTEGER,
	"property"	TEXT,
	"value"	TEXT
);
CREATE TABLE IF NOT EXISTS "Tags" (
	"id"	INTEGER,
	"pid"	INTEGER,
	"name"	TEXT NOT NULL,
	"icon"	INTEGER,
	"iconkde"	TEXT,
	PRIMARY KEY("id"),
	UNIQUE("name","pid")
);
CREATE TABLE IF NOT EXISTS "TagsTree" (
	"id"	INTEGER NOT NULL,
	"pid"	INTEGER NOT NULL,
	UNIQUE("id","pid")
);
CREATE TABLE IF NOT EXISTS "VideoMetadata" (
	"imageid"	INTEGER,
	"aspectRatio"	TEXT,
	"audioBitRate"	TEXT,
	"audioChannelType"	TEXT,
	"audioCompressor"	TEXT,
	"duration"	TEXT,
	"frameRate"	TEXT,
	"exposureProgram"	INTEGER,
	"videoCodec"	TEXT,
	PRIMARY KEY("imageid")
);
CREATE INDEX IF NOT EXISTS "comments_imageid_index" ON "ImageComments" (
	"imageid"
);
CREATE INDEX IF NOT EXISTS "copyright_imageid_index" ON "ImageCopyright" (
	"imageid"
);
CREATE INDEX IF NOT EXISTS "creationdate_index" ON "ImageInformation" (
	"creationDate"
);
CREATE INDEX IF NOT EXISTS "dir_index" ON "Images" (
	"album"
);
CREATE INDEX IF NOT EXISTS "hash_index" ON "Images" (
	"uniqueHash"
);
CREATE INDEX IF NOT EXISTS "image_name_index" ON "Images" (
	"name"
);
CREATE INDEX IF NOT EXISTS "imagetagproperties_imageid_index" ON "ImageTagProperties" (
	"imageid"
);
CREATE INDEX IF NOT EXISTS "imagetagproperties_index" ON "ImageTagProperties" (
	"imageid",
	"tagid"
);
CREATE INDEX IF NOT EXISTS "imagetagproperties_tagid_index" ON "ImageTagProperties" (
	"tagid"
);
CREATE INDEX IF NOT EXISTS "object_relations_index" ON "ImageRelations" (
	"object"
);
CREATE INDEX IF NOT EXISTS "subject_relations_index" ON "ImageRelations" (
	"subject"
);
CREATE INDEX IF NOT EXISTS "tag_id_index" ON "ImageTags" (
	"imageid"
);
CREATE INDEX IF NOT EXISTS "tag_index" ON "ImageTags" (
	"tagid"
);
CREATE INDEX IF NOT EXISTS "tagproperties_index" ON "TagProperties" (
	"tagid"
);
CREATE INDEX IF NOT EXISTS "uuid_index" ON "ImageHistory" (
	"uuid"
);
CREATE TRIGGER delete_album DELETE ON Albums
                BEGIN
                    DELETE FROM Images
                    WHERE Images.album = OLD.id;
                END;
CREATE TRIGGER delete_albumroot DELETE ON AlbumRoots
                    BEGIN
                        DELETE FROM Albums
                        WHERE Albums.albumRoot = OLD.id;
                    END;
CREATE TRIGGER delete_image DELETE ON Images
                    BEGIN
                        DELETE FROM ImageTags          WHERE imageid=OLD.id;
                        DELETE From ImageInformation   WHERE imageid=OLD.id;
                        DELETE From ImageMetadata      WHERE imageid=OLD.id;
                        DELETE From VideoMetadata      WHERE imageid=OLD.id;
                        DELETE From ImagePositions     WHERE imageid=OLD.id;
                        DELETE From ImageComments      WHERE imageid=OLD.id;
                        DELETE From ImageCopyright     WHERE imageid=OLD.id;
                        DELETE From ImageProperties    WHERE imageid=OLD.id;
                        DELETE From ImageHistory       WHERE imageid=OLD.id;
                        DELETE FROM ImageRelations     WHERE subject=OLD.id OR object=OLD.id;
                        DELETE FROM ImageTagProperties WHERE imageid=OLD.id;
                        UPDATE Albums SET icon=null    WHERE icon=OLD.id;
                        UPDATE Tags SET icon=null      WHERE icon=OLD.id;
                    END;
CREATE TRIGGER delete_tag DELETE ON Tags
                    BEGIN
                        DELETE FROM ImageTags WHERE tagid=OLD.id;
                        DELETE FROM TagProperties WHERE tagid=OLD.id;
                        DELETE FROM ImageTagProperties WHERE tagid=OLD.id;
                    END;
CREATE TRIGGER delete_tagstree DELETE ON Tags
                    BEGIN
                        DELETE FROM Tags
                        WHERE id  IN (SELECT id FROM TagsTree WHERE pid=OLD.id);
                        DELETE FROM TagsTree
                        WHERE id IN (SELECT id FROM TagsTree WHERE pid=OLD.id);
                        DELETE FROM TagsTree
                        WHERE id=OLD.id;
                    END;
CREATE TRIGGER insert_tagstree AFTER INSERT ON Tags
                    BEGIN
                    INSERT INTO TagsTree
                        SELECT NEW.id, NEW.pid
                        UNION
                        SELECT NEW.id, pid FROM TagsTree WHERE id=NEW.pid;
                    END;
CREATE TRIGGER move_tagstree UPDATE OF pid ON Tags
                    BEGIN
                        DELETE FROM TagsTree
                            WHERE
                            ((id = OLD.id)
                            OR
                            id IN (SELECT id FROM TagsTree WHERE pid=OLD.id))
                            AND
                            pid IN (SELECT pid FROM TagsTree WHERE id=OLD.id);
                        INSERT INTO TagsTree
                            SELECT NEW.id, NEW.pid
                            UNION
                            SELECT NEW.id, pid FROM TagsTree WHERE id=NEW.pid
                            UNION
                            SELECT id, NEW.pid FROM TagsTree WHERE pid=NEW.id
                            UNION
                            SELECT A.id, B.pid FROM TagsTree A, TagsTree B
                            WHERE
                            A.pid = NEW.id AND B.id = NEW.pid;
                    END;
COMMIT;
