API Docs
========

TROVE provides a few useful API endpoints:

1. Get the scores for all candidates from a nonlocalized event name (e.g., S251112cm)
2. Get the scores for all candidates in a cone search from a nonlocalized event name. This can be useful for, e.g., prioritizing candidates for followup with a telescope.
3. Uploading new targets and/or new photometry.

API Quickstart
--------------
Details and examples with curl commands to access the endpoints are on the RESTful API Reference docs
page: https://datatrove.as.arizona.edu/api/docs . [#f1]_

An example curl command is:

.. code-block:: bash
		
   curl -X 'GET' \
       'https://datatrove.as.arizona.edu/api/score/S251112cm' \
       -u '<username>:<password>'

where you replace <username> with your username and <password> with your password. If you are doing this in python,
see the `Example Jupyter Notebooks <apiexample>`_.

.. toctree::
   :maxdepth: 2
   :hidden:

   Example Notebook <nbs/basic_usage>
   API Reference <https://datatrove.as.arizona.edu/docs/api>
   
   
.. rubric:: Footnotes
.. [#f1] If you're a developer working on the :file:`integration` branch, you'll want https://datatrove-test.as.arizona.edu/api/docs. If on :file:`staging`, go to https://datatrove-staging.as.arizona.edu/api/docs.  
