"""Train the custom "Hey Mary" wake word locally: record -> train -> test_model.

Everything runs on Windows without PyTorch: openWakeWord's own feature models (bundled with the
package) turn audio into embeddings, and a small classifier on top of them is the wake word.
"""
