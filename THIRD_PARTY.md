# Third-party components and data

The repository includes the 3Dmol.js 2.5.5 browser bundle under the BSD 3-Clause license. Its [license](src/protein_explorer/static/vendor/3Dmol-LICENSE.txt) and [package provenance](src/protein_explorer/static/vendor/provenance.json) accompany the bundle.

The search pipeline downloads [ESM-2](https://huggingface.co/facebook/esm2_t6_8M_UR50D) separately. The model revision is pinned in the search implementation and recorded in each index. Model files are excluded from Git.

Structure prediction uses [ColabFold](https://github.com/sokrypton/ColabFold) and [AlphaFold](https://github.com/google-deepmind/alphafold). Their software and model parameters retain their upstream terms. Container images, dependencies, and model parameters are downloaded separately and are excluded from Git.

The corpus preparation script retrieves public [RCSB PDB](https://www.rcsb.org/) records, preserves source URLs and CC0 attribution metadata, and records the collection date. Experimental PDB structures are fetched on selection. Corpus files and downloaded structures are excluded from Git; documentation measurements identify their public reference samples.
