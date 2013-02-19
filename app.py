import flask
import json
import os
from PIL import Image, ImageFile
from gevent.event import AsyncResult, Timeout
from gevent.queue import Empty, Queue
from shutil import rmtree
from hashlib import sha1
from stat import S_ISREG, ST_CTIME, ST_MODE


DATA_DIR = 'data'
KEEP_ALIVE_DELAY = 25
MAX_IMAGE_SIZE = 800, 600
MAX_IMAGES = 10

app = flask.Flask(__name__, static_folder=DATA_DIR)
broadcast_queue = Queue()


try:  # Reset saved files on each start
    rmtree(DATA_DIR, True)
    os.mkdir(DATA_DIR)
except OSError:
    pass


def broadcast(message):
    """Notify all waiting waiting gthreads of message."""
    waiting = []
    try:
        while True:
            waiting.append(broadcast_queue.get(block=False))
    except Empty:
        pass
    print('Broadcasting {0} messages'.format(len(waiting)))
    for item in waiting:
        item.set(message)


def receive():
    """Generator that yields a message at least every KEEP_ALIVE_DELAY seconds.

    yields messages sent by `broadcast`.

    """
    tmp = None
    while True:
        if not tmp:
            tmp = AsyncResult()
            broadcast_queue.put(tmp)
        try:
            yield tmp.get(timeout=KEEP_ALIVE_DELAY)
            tmp = None
        except Timeout:
            yield ''


def save_normalized_image(path, data):
    image_parser = ImageFile.Parser()
    try:
        image_parser.feed(data)
        image = image_parser.close()
    except IOError:
        raise
        return False
    image.thumbnail(MAX_IMAGE_SIZE, Image.ANTIALIAS)
    if image.mode != 'RGB':
        image = image.convert('RGB')
    image.save(path)
    return True


def event_stream(client):
    try:
        for message in receive():
            yield 'data: {0}\n\n'.format(message)
    finally:
        print('{0} disconnected from stream'.format(client))


@app.route('/post', methods=['POST'])
def post():
    sha1sum = sha1(flask.request.data).hexdigest()
    target = os.path.join(DATA_DIR, '{0}.jpg'.format(sha1sum))
    message = json.dumps({'src': target,
                          'ip_addr': flask.request.access_route[0]})
    try:
        if save_normalized_image(target, flask.request.data):
            broadcast(message)  # Notify subscribers of completion
    except Exception as e:  # Output errors
        print e
    return ''


@app.route('/stream')
def stream():
    return flask.Response(event_stream(flask.request.access_route[0]),
                          mimetype='text/event-stream')


@app.route('/')
def home():
    # Code adapted from: http://stackoverflow.com/questions/168409/
    image_infos = []
    for filename in os.listdir(DATA_DIR):
        filepath = os.path.join(DATA_DIR, filename)
        file_stat = os.stat(filepath)
        if S_ISREG(file_stat[ST_MODE]):
            image_infos.append((file_stat[ST_CTIME], filepath))

    images = []
    for i, (_, path) in enumerate(sorted(image_infos, reverse=True)):
        if i >= MAX_IMAGES:
            os.unlink(path)
            continue
        images.append('<div><img alt="User uploaded image" src="{0}" /></div>'
                      .format(path))
    return """
<!doctype html>
<title>Image Uploader</title>
<meta charset="utf-8" />
<script src="//ajax.googleapis.com/ajax/libs/jquery/1.9.1/jquery.min.js"></script>
<script src="//ajax.googleapis.com/ajax/libs/jqueryui/1.10.1/jquery-ui.min.js"></script>
<style>
  body {
    max-width: 800px;
    margin: auto;
    padding: 1em;
    background: black;
    color: #fff;
    font: 16px/1.6 menlo, monospace;
    text-align:center;
  }

  a {
    color: #fff;
  }
</style>
<h3>Image Uploader</h3>
<p>Upload an image for everyone to see. Valid images are pushed to everyone
currently connected, and only the most recent %s images are saved.</p>
<p>The complete source for this Flask web service can be found at:
<a href="https://github.com/bboe/flask-image-uploader">https://github.com/bboe/flask-image-uploader</a></p>
<p>Disclaimer: The author of this application accepts no responsibility for the
images uploaded to this web service. To discourage the submission of obscene images, IP
addresses will be visibly associated with uploaded images.</p>
<noscript>Note: You must have javascript enabled in order to upload and
dynamically view new images.</noscript>
<p>Select an image: <input id="file" type="file" /></p>
<h3>Uploaded Images (updated in real-time)</h3>
<div id="images">%s</div>
<script>
  function sse() {
      var source = new EventSource('/stream');
      source.onmessage = function(e) {
          if (e.data == '')
              return;
          var data = $.parseJSON(e.data);
          console.log(data);
          var upload_message = 'Image uploaded by ' + data['ip_addr'];
          var image = $('<img>', {alt: upload_message, src: data['src']});
          var container = $('<div>').hide();
          container.append($('<div>', {text: upload_message}));
          container.append(image);
          $('#images').prepend(container);
          image.load(function(){
              container.show('blind', {}, 1000);
          });
      };
  }
  $('#file').change(function(e){
      var xhr = new XMLHttpRequest();
      xhr.open('POST', '/post', true);
      xhr.send(e.target.files[0]);
      e.target.value = '';
  });
  sse();
</script>
""" % (MAX_IMAGES, '\n'.join(images))


if __name__ == '__main__':
    app.debug = True
    app.run('0.0.0.0', threaded=True)
