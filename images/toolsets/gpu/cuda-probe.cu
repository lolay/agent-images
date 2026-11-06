// agent-cuda-probe: proof that CUDA code built into the image runs on the GPU the
// execution has (the gpu toolset's smoke check). Built by install.sh for the L4
// (sm_89) and Blackwell (sm_120), with PTX for newer GPUs. Prints the device and
// exits 0 only if a kernel ran and its result checks out; a GPU this binary
// wasn't built for fails with "no kernel image".
#include <cstdio>
#include <cuda_runtime.h>

__global__ void add(const float *a, const float *b, float *c, int n) {
	int i = blockIdx.x * blockDim.x + threadIdx.x;
	if (i < n) c[i] = a[i] + b[i];
}

static int fail(const char *what, cudaError_t err) {
	std::fprintf(stderr, "agent-cuda-probe: %s: %s\n", what, cudaGetErrorString(err));
	return 1;
}

int main() {
	int count = 0;
	cudaError_t err = cudaGetDeviceCount(&count);
	if (err != cudaSuccess) return fail("no CUDA device", err);

	cudaDeviceProp prop;
	if ((err = cudaGetDeviceProperties(&prop, 0)) != cudaSuccess) return fail("device properties", err);
	int driver = 0, runtime = 0;
	cudaDriverGetVersion(&driver);
	cudaRuntimeGetVersion(&runtime);

	const int n = 1 << 20;
	float *a, *b, *c;
	if ((err = cudaMallocManaged(&a, n * sizeof(float))) != cudaSuccess) return fail("allocate", err);
	cudaMallocManaged(&b, n * sizeof(float));
	cudaMallocManaged(&c, n * sizeof(float));
	for (int i = 0; i < n; i++) {
		a[i] = float(i);
		b[i] = float(2 * i);
	}
	add<<<(n + 255) / 256, 256>>>(a, b, c, n);
	if ((err = cudaGetLastError()) != cudaSuccess) return fail("launch", err);
	if ((err = cudaDeviceSynchronize()) != cudaSuccess) return fail("run", err);
	for (int i = 0; i < n; i++) {
		if (c[i] != float(3 * i)) {
			std::fprintf(stderr, "agent-cuda-probe: wrong result at %d\n", i);
			return 1;
		}
	}
	cudaFree(a);
	cudaFree(b);
	cudaFree(c);

	std::printf("%s, compute %d.%d, %d SMs, %.0f GiB, driver CUDA %d.%d, runtime %d.%d: kernel ok\n",
		prop.name, prop.major, prop.minor, prop.multiProcessorCount,
		prop.totalGlobalMem / 1073741824.0, driver / 1000, (driver % 1000) / 10,
		runtime / 1000, (runtime % 1000) / 10);
	return 0;
}
