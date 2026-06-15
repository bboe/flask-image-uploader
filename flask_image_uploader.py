"""A small Flask application demonstrating HTML Server-Sent Events.

Visitors can upload an image that is broadcast in real time, over an ``EventSource``
stream, to every other connected visitor. Only the most recent images are retained on
disk.

"""

from __future__ import annotations

import json
import logging
import time
from hashlib import sha1
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING

import flask
from gevent.event import AsyncResult
from gevent.queue import Empty, Queue
from gevent.timeout import Timeout
from PIL import Image, ImageFile, UnidentifiedImageError

if TYPE_CHECKING:
    from collections.abc import Iterator

try:
    __version__ = version("flask-image-uploader")
except PackageNotFoundError:
    __version__ = "unknown"

DATA_DIR = Path("data")
KEEP_ALIVE_DELAY = 25
MAX_DURATION = 300
MAX_IMAGES = 10
MAX_IMAGE_SIZE = (800, 600)

PAGE_TEMPLATE = """\
<!doctype html>
<title>Image Uploader</title>
<meta charset="utf-8" />
<script src="//ajax.googleapis.com/ajax/libs/jquery/1.9.1/jquery.min.js"></script>
<script src="//ajax.googleapis.com/ajax/libs/jqueryui/1.10.1/jquery-ui.min.js"></script>
<link rel="stylesheet"
  href="//ajax.googleapis.com/ajax/libs/jqueryui/1.10.1/themes/vader/jquery-ui.css" />
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

  .notice {
    font-size: 80%;
  }


#drop {
    font-weight: bold;
    text-align: center;
    padding: 1em 0;
    margin: 1em 0;
    color: #555;
    border: 2px dashed #555;
    border-radius: 7px;
    cursor: default;
}

#drop.hover {
    color: #f00;
    border-color: #f00;
    border-style: solid;
    box-shadow: inset 0 3px 4px #888;
}

</style>
<h3>Image Uploader</h3>
<p>Upload an image for everyone to see. Valid images are pushed to everyone
currently connected, and only the most recent {{ max_images }} images are saved.</p>
<p>The complete source for this Flask web service can be found at:
<a href="https://github.com/bboe/flask-image-uploader">https://github.com/bboe/flask-image-uploader</a></p>
<p class="notice">Disclaimer: The author of this application accepts no responsibility for the
images uploaded to this web service. To discourage the submission of obscene images, IP
addresses with the last two octets hidden will be visibly associated with uploaded images.</p>
<noscript>Note: You must have javascript enabled in order to upload and
dynamically view new images.</noscript>
<fieldset>
  <p id="status">Select an image</p>
  <div id="progressbar"></div>
  <input id="file" type="file" />
  <div id="drop">or drop image here</div>
</fieldset>
<h3>Uploaded Images (updated in real-time)</h3>
<div id="images">
{% for image in images %}<div><img alt="User uploaded image" src="{{ image }}" /></div>
{% endfor %}</div>
<script>
  function sse() {
      var source = new EventSource('/stream');
      source.onmessage = function(e) {
          if (e.data == '')
              return;
          var data = $.parseJSON(e.data);
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
  function file_select_handler(to_upload) {
      var progressbar = $('#progressbar');
      var status = $('#status');
      var xhr = new XMLHttpRequest();
      xhr.upload.addEventListener('loadstart', function(e1){
          status.text('uploading image');
          progressbar.progressbar({max: e1.total});
      });
      xhr.upload.addEventListener('progress', function(e1){
          if (progressbar.progressbar('option', 'max') == 0)
              progressbar.progressbar('option', 'max', e1.total);
          progressbar.progressbar('value', e1.loaded);
      });
      xhr.onreadystatechange = function(e1) {
          if (this.readyState == 4)  {
              if (this.status == 200)
                  var text = 'upload complete: ' + this.responseText;
              else
                  var text = 'upload failed: code ' + this.status;
              status.html(text + '<br/>Select an image');
              progressbar.progressbar('destroy');
          }
      };
      xhr.open('POST', '/post', true);
      xhr.send(to_upload);
  };
  function handle_hover(e) {
      e.originalEvent.stopPropagation();
      e.originalEvent.preventDefault();
      e.target.className = (e.type == 'dragleave' || e.type == 'drop') ? '' : 'hover';
  }

  $('#drop').bind('drop', function(e) {
      handle_hover(e);
      if (e.originalEvent.dataTransfer.files.length < 1) {
          return;
      }
      file_select_handler(e.originalEvent.dataTransfer.files[0]);
  }).bind('dragenter dragleave dragover', handle_hover);
  $('#file').change(function(e){
      file_select_handler(e.target.files[0]);
      e.target.value = '';
  });
  sse();
</script>
"""

app = flask.Flask(__name__, static_folder=str(DATA_DIR))
broadcast_queue: Queue[AsyncResult[str]] = Queue()


logger = logging.getLogger(__name__)


def broadcast(message: str) -> None:
    """Notify every waiting greenlet of ``message``."""
    waiting: list[AsyncResult[str]] = []
    try:
        while True:
            waiting.append(broadcast_queue.get(block=False))
    except Empty:
        pass
    logger.info("Broadcasting %d messages", len(waiting))
    for result in waiting:
        result.set(message)


def event_stream(client: str) -> Iterator[str]:
    """Yield Server-Sent Event payloads for ``client`` as messages arrive."""
    try:
        for message in receive():
            yield f"data: {message}\n\n"
        logger.info("%s force closing stream", client)
    finally:
        logger.info("%s disconnected from stream", client)


@app.route("/")
def home() -> str:
    """Render the upload form and the most recent images.

    Returns:
        The rendered HTML page.

    """
    entries = sorted(
        (entry for entry in DATA_DIR.iterdir() if entry.is_file()),
        key=lambda entry: entry.stat().st_ctime,
        reverse=True,
    )
    images: list[str] = []
    for index, entry in enumerate(entries):
        if index >= MAX_IMAGES:
            entry.unlink()
            continue
        images.append(str(entry))
    return flask.render_template_string(PAGE_TEMPLATE, images=images, max_images=MAX_IMAGES)


def main() -> None:
    """Run the development server."""
    logging.basicConfig(level=logging.INFO)
    DATA_DIR.mkdir(exist_ok=True)
    app.run("127.0.0.1", threaded=True)


@app.route("/post", methods=["POST"])
def post() -> str:
    """Handle an image upload and broadcast it on success.

    Returns:
        ``"success"`` once the upload is handled, or ``"failure"`` if processing raised
        an unexpected error.

    """
    digest = sha1(flask.request.data, usedforsecurity=False).hexdigest()
    target = DATA_DIR / f"{digest}.jpg"
    message = json.dumps({"ip_addr": safe_addr(flask.request.access_route[0]), "src": str(target)})
    try:
        if save_normalized_image(target, flask.request.data):
            broadcast(message)
    except Exception:
        logger.exception("Failed to process upload")
        return "failure"
    return "success"


def receive() -> Iterator[str]:
    """Yield broadcast messages, emitting a keep-alive at least periodically.

    A blank message is yielded every ``KEEP_ALIVE_DELAY`` seconds so that idle
    connections are not closed by intermediaries. Streams are capped at ``MAX_DURATION``
    seconds because some hosts do not notify the server when a client disconnects.

    Yields:
        Messages sent via :func:`broadcast`, interspersed with empty keep-alive strings.

    """
    end = time.monotonic() + MAX_DURATION
    result: AsyncResult[str] | None = None
    while time.monotonic() < end:
        if result is None:
            result = AsyncResult()
            broadcast_queue.put(result)
        try:
            yield result.get(timeout=KEEP_ALIVE_DELAY) or ""
            result = None
        except Timeout:
            yield ""


def safe_addr(ip_addr: str) -> str:
    """Return ``ip_addr`` with its trailing two octets masked."""
    return ".".join([*ip_addr.split(".")[:2], "xxx", "xxx"])


def save_normalized_image(path: Path, data: bytes) -> bool:
    """Save an RGB thumbnail of the image in ``data`` to ``path``.

    Returns:
        ``True`` if the image was decoded and saved, ``False`` if ``data`` could not be
        parsed as an image.

    """
    parser = ImageFile.Parser()
    try:
        parser.feed(data)
        image = parser.close()
    except (OSError, UnidentifiedImageError):
        return False
    image.thumbnail(MAX_IMAGE_SIZE, Image.Resampling.LANCZOS)
    if image.mode != "RGB":
        image = image.convert("RGB")
    image.save(path)
    return True


@app.route("/stream")
def stream() -> flask.Response:
    """Serve a long-lived Server-Sent Events stream.

    Returns:
        A streaming ``text/event-stream`` response.

    """
    return flask.Response(
        event_stream(flask.request.access_route[0]),
        mimetype="text/event-stream",
    )


if __name__ == "__main__":
    main()
