import boto3
from boto3.dynamodb.conditions import Key, Attr
import json
import os
import sys
import jinja2
import wave
import contextlib
from mutagen.mp3 import MP3
import urllib
import datetime
import dateutil.parser
import re
from pyexiftool import exiftool

# Environment variables
region_name = os.getenv('REGION')
collection_table_name = os.getenv('COLLECTION_TABLE_NAME')
archive_table_name = os.getenv('ARCHIVE_TABLE_NAME')
archive_id = os.getenv('ARCHIVE_ID')

try:
    dyndb = boto3.resource('dynamodb', region_name=region_name)
    archive_table = dyndb.Table(archive_table_name)
    collection_table = dyndb.Table(collection_table_name)    
except Exception as e:
    print(f"An error occurred: {str(e)}")
    raise e


def fetchArchive(archive_id):
    ret_val = None
    try:
        response = archive_table.query(
            KeyConditionExpression=Key('id').eq(archive_id),
            Limit=1
        )
        ret_val = response['Items'][0]
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        raise e
    return ret_val
    

def fetchCollection(collection_id):
    ret_val = None
    try:
        response = collection_table.query(
            KeyConditionExpression=Key('id').eq(collection_id),
            Limit=1
        )
        ret_val = response['Items'][0]
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        raise e
    return ret_val


def fetchAllArchivesForCollection(collection_id):
    scan_kwargs = {
        'FilterExpression': Attr('heirarchy_path').contains(collection_id)
    }
    source_table_items = []
    try:
        done = False
        start_key = None
        while not done:
            if start_key:
                scan_kwargs['ExclusiveStartKey'] = start_key
            response = archive_table.scan(**scan_kwargs)
            source_table_items.extend(response['Items'])
            start_key = response.get('LastEvaluatedKey', None)
            done = start_key is None
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        raise e
    return source_table_items


def getAudioFile(audioFileURL, cwd):
    response = urllib.request.urlopen(audioFileURL)
    data = response.read()

    tmpDir = f"{cwd}/tmp"
    if not os.path.isdir(tmpDir):
        os.mkdir(tmpDir)

    a = urllib.parse.urlparse(audioFileURL)    
    filename = os.path.basename(a.path)
    tmpFile = f"{tmpDir}/{filename}"

    f = open(tmpFile, 'wb')
    f.write(data)
    return tmpFile

    
def getAudioFileMetadata(audioFileName):
    _, file_extension = os.path.splitext(audioFileName)
    metadata = {}
    if file_extension == ".wav":
        with contextlib.closing(wave.open(audioFileName,'r')) as f:
            frames = f.getnframes()
            rate = f.getframerate()
            duration = frames / float(rate)
            metadata['duration'] = duration
    elif file_extension == ".mp3":  
        audio = MP3(audioFileName)
        if audio is not None:
            metadata['length'] = os.path.getsize(audioFileName)
            metadata['duration'] = int(audio.info.length)
            metadata['file_type'] = "audio/mpeg"

    os.remove(audioFileName)
    return metadata


def setCollectionMetadata(collection):
    metadata = {}
    if "title" in collection:
            metadata["title"] = collection["title"]
    if "description" in collection:
        metadata["description"] = collection["description"]
    metadata["itunes_type"] = ""
    if "rights_holder" in collection:
        metadata["copyright"] = f"{datetime.datetime.now().year} {collection['rights_holder']}"
    if "creator" in collection:    
        metadata["itunes_owner"] = collection["creator"]
        metadata["googleplay_author"] = collection["creator"]
    if "thumbnail_path" in collection:
        metadata["img_url"] = collection["thumbnail_path"]
    if "language" in collection:
        metadata["language"] = collection["language"]
    else:
        metadata["language"] = "en"
    metadata["link"] = f"https://podcasts.lib.vt.edu/collection/{collection['custom_key'].replace('ark:/53696/', '')}"

    return metadata


def setArchivesMetadata(collectionArchives, cwd):
    items = []
    for archive in collectionArchives:
        item = {}
        if "title" in archive:
            item["title"] = archive["title"]
        if "description" in archive:
            item["description"] = re.sub(r'(\s*\n+\s*)', " ", archive["description"])
        if "createdAt" in archive:
            dt = dateutil.parser.parse(archive["createdAt"])
            date_formatted = dt.strftime("%a, %w %b %Y %H:%M:%S %Z")
            item["pubDate"] = date_formatted
        if "thumbnail_path" in archive:
            item["img_url"] = archive["thumbnail_path"]
        if "manifest_url" in archive:
            audioFileName = archive['manifest_url']
            localFilePath = getAudioFile(audioFileName, cwd)
            audioFileMetadata = getAudioFileMetadata(localFilePath)
            
            item["url"] = archive['manifest_url']
            item["length"] = audioFileMetadata["length"]
            item["duration"] = audioFileMetadata["duration"]
            item["file_type"] = audioFileMetadata["file_type"]
            item["guid_permaLink"] = ""
            item["guid"] = ""

        items.append(item)
    return items


def createRSSContent(metadata):
    templateLoader = jinja2.FileSystemLoader(searchpath="./templates/")
    templateEnv = jinja2.Environment(loader=templateLoader)
    TEMPLATE_FILE = "podcast.rss.jinja"
    template = templateEnv.get_template(TEMPLATE_FILE)
    return template.render(podcast = metadata, items = metadata["items"])


def writeContentToFile(rssContent, collectionKey, cwd):
    keyPrefix = "ark:/53696/"
    filename = f"{collectionKey.replace(keyPrefix, '')}.rss"
    outputDir = f"{cwd}/output"
    if not os.path.isdir(outputDir):
        os.mkdir(outputDir)

    outputFile = f"{outputDir}/{filename}"

    f = open(outputFile, 'w')
    f.write(rssContent)

    if os.path.isfile(outputFile):
        print(f"Output written to {outputFile}")
    
            
def lambda_handler(event, context):
    metadata = None
    collection = None
    cwd = os.getcwd()
    
    targetArchive = fetchArchive(event['archive_id'])
    if 'parent_collection' in targetArchive:
        collection = fetchCollection(targetArchive['parent_collection'][0])
        collectionArchives = fetchAllArchivesForCollection(collection['id'])
        metadata = setCollectionMetadata(collection)
        metadata["items"] = setArchivesMetadata(collectionArchives, cwd)

        rssContent = createRSSContent(metadata)
        writeContentToFile(rssContent, collection["custom_key"], cwd)

    return {
        'statusCode': 200,
        'body': json.dumps('Hello from Lambda!')
    }

if __name__ == "__main__":
    lambda_event = {"archive_id": archive_id}
    lambda_handler(lambda_event, None)