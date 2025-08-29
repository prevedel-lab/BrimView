import param
import brimfile as bls

class bls_param(param.Parameterized):
    file = param.ClassSelector(class_=bls.File, default=None, allow_None=True, allow_refs=True)
    data = param.ClassSelector(class_=bls.Data, default=None, allow_None=True, allow_refs=True)
    analysis = param.ClassSelector(class_=bls.Data.AnalysisResults, default=None, allow_None=True, allow_refs=True)

    def __init__(self, **params):
        super().__init__(**params)

        # Explicit annotation, because param and type hinting is not working properly
        self.file: bls.File
        self.data: bls.Data
        self.analysis: bls.Data.AnalysisResults
    
    def reset(self):
        print("Resetting bls_input")
        with param.parameterized.batch_call_watchers(self):
            self.file = None
            self.data = None
            self.analysis = None