import os
from django.views.generic import TemplateView
from django.http import FileResponse, Http404
from django.conf import settings


class DocsView(TemplateView):
    """Serve static Sphinx-generated documentation."""

    def get(self, request, *args, **kwargs):
        # Get the requested path (e.g., "getting-started/index.html")
        doc_path = kwargs.get("path", "index.html")

        # Ensure the path is safe (prevent directory traversal)
        if ".." in doc_path or doc_path.startswith("/"):
            raise Http404("Invalid documentation path")

        # Build the full file path
        docs_dir = os.path.join(
            settings.BASE_DIR, "sphinx_docs", "docs", "build", "html"
        )
        file_path = os.path.join(docs_dir, doc_path)

        # Ensure the resolved path is still within docs_dir
        file_path = os.path.normpath(file_path)
        if not file_path.startswith(os.path.normpath(docs_dir)):
            raise Http404("Documentation not found")

        # If it's a directory, serve index.html
        if os.path.isdir(file_path):
            file_path = os.path.join(file_path, "index.html")

        # Check if file exists
        if not os.path.isfile(file_path):
            raise Http404("Documentation page not found")

        # Serve the file
        return FileResponse(open(file_path, "rb"))
