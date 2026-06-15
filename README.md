# flask-image-uploader

A small Flask application demonstrating HTML Server-Sent Events
([`EventSource`](https://developer.mozilla.org/docs/Web/API/EventSource)).
Visitors upload an image and it is pushed, in real time, to every other
connected visitor. Only the most recent images are retained on disk.

Inspired by: https://github.com/jkbr/chat

### Requirements

`flask-image-uploader` supports Python 3.10 through 3.14.

### Running the development server

Run the app directly with [uv](https://docs.astral.sh/uv/):

    uv run flask-image-uploader

Then open [http://127.0.0.1:5000](http://127.0.0.1:5000) and upload an image.
Uploaded images appear in every connected browser as they arrive.

### Running with gunicorn

The streaming endpoint relies on cooperative greenlets, so serve it with a
`gevent` worker:

    uv run gunicorn --worker-class gevent --timeout 300 flask_image_uploader:app
