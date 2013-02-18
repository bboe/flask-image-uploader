#!/usr/bin/env python
import flask
import redis
import os
from hashlib import sha1
from stat import S_ISREG, ST_CTIME, ST_MODE


DATA_DIR = 'data'

app = flask.Flask(__name__, static_folder=DATA_DIR)
red = redis.from_url(os.getenv('REDISTOGO_URL', 'redis://localhost:6379'))


try:
    os.mkdir(DATA_DIR)
except OSError:
    pass


def event_stream():
    pubsub = red.pubsub()
    pubsub.subscribe('processed')
    for message in pubsub.listen():
        print message
        if message['type'] == 'message':
            yield 'data: {0}\n\n'.format(message['data'])


@app.route('/post', methods=['POST'])
def post():
    sha1sum = sha1(flask.request.data).hexdigest()
    target = os.path.join(DATA_DIR, '{0}.jpg'.format(sha1sum))
    if not os.path.isfile(target):  # Save the file to disk
        with open(target, 'wb') as fp:
            fp.write(flask.request.data)
    # Notify subscribers of completion
    red.publish('processed', target)
    return ''


@app.route('/stream')
def stream():
    return flask.Response(event_stream(),
                          mimetype="text/event-stream")


@app.route('/')
def home():
    # Code adapted from: http://stackoverflow.com/questions/168409/
    image_infos = []
    for filename in os.listdir(DATA_DIR):
        filepath = os.path.join(DATA_DIR, filename)
        file_stat = os.stat(filepath)
        if S_ISREG(file_stat[ST_MODE]):
            image_infos.append((file_stat[ST_CTIME], filepath, filename[:-4]))
    images = '\n'.join('<div id="{0}"><img src="{1}" /></div>'.format(x[2], x[1])
                       for x in sorted(image_infos, reverse=True))
    return """
<!doctype html>
<title>Image Uploader</title>
<meta charset="utf-8" />
<script src="//ajax.googleapis.com/ajax/libs/jquery/1.9.1/jquery.min.js"></script>
<script src="//ajax.googleapis.com/ajax/libs/jqueryui/1.10.1/jquery-ui.min.js"></script>
<style>
  body {
    max-width: 500px;
    margin: auto;
    padding: 1em;
    background: black;
    color: #fff;
    font: 16px/1.6 menlo, monospace;
  }
</style>
<h3>Image Uploader</h3>
<p>Image: <input id="file" type="file" /></p>
<h3>Uploaded Image</h3>
<div id="images">%s</div>
<script>
  function sse() {
      var source = new EventSource('/stream');
      source.onmessage = function(e) {
          console.log(e.data);
          var image = $('<img>', {src: e.data}).hide();
          $('#images').prepend(image);
          image.load(function(){
              image.show('blind', {}, 1000);
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
""" % images


if __name__ == '__main__':
    #app.debug = True
    app.run('0.0.0.0', threaded=True)
